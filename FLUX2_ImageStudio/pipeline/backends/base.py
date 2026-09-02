# -*- coding: utf-8 -*-
"""
后端抽象基类 — ComfyUI 通用能力 + 后端注册表
==============================================
- registry.json 集中描述"有哪些后端、什么类型、用什么工作流、默认参数"
- 每个后端一个 adapter 子类，只负责"工作流节点改写"（build_workflow）
- 提交 / 等待 / 取回 是 ComfyUI 通用协议，放基类，子类不重复实现
- 输出取回：提交前把 SaveVideo/SaveImage 的 filename_prefix 设为本次运行 UUID，
  完成后按前缀精确取回，不再用 mtime 猜文件

用法：
    from backends.base import get_backend, list_backends
    backend = get_backend("wan22", server_url="http://127.0.0.1:8188")
    wf = backend.build_workflow(prompt=..., image=..., seed=42, width=832, height=480, length=81)
    pid = backend.submit(wf)
    backend.wait(pid)
    out = backend.fetch_output()
"""

import json
import time
import uuid
from pathlib import Path

import urllib.request
import urllib.error

# 本机服务直连，禁用系统代理（否则 127.0.0.1 被代理拦截导致连接拒绝）
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({})))


def _request_retry(url, data=None, headers=None, timeout=30, tries=4, delay=1.5):
    """带自动重试的 HTTP 请求（应对瞬时连接拒绝/抖动）"""
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers or {})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = e
            if 400 <= e.code < 500 and e.code != 404:
                raise  # 校验类错误重试无意义
        except Exception as e:
            last = e
        time.sleep(delay * (i + 1))
    raise last

PIPELINE_ROOT = Path(__file__).resolve().parent.parent
BACKENDS_DIR = Path(__file__).resolve().parent
CONFIG_DIR = PIPELINE_ROOT / "config"
DEFAULT_COMFY_OUTPUT = (
    PIPELINE_ROOT.parent / "ComfyUI_windows_portable" / "ComfyUI" / "output"
)


# ============ 注册表 ============

def load_registry() -> dict:
    """读取 backends/registry.json，返回 {backend_id: 定义}"""
    reg_file = BACKENDS_DIR / "registry.json"
    if not reg_file.exists():
        raise RuntimeError(f"缺少后端注册表: {reg_file}")
    return json.loads(reg_file.read_text(encoding="utf-8"))


def list_backends() -> list:
    """返回所有已注册后端 id（按注册表顺序）"""
    return list(load_registry().keys())


def get_backend(backend_id: str, server_url: str = "http://127.0.0.1:8188"):
    """按 id 实例化后端。backend_id 形如 'h3' / 'wan22' / 'sdxl'。"""
    registry = load_registry()
    if backend_id not in registry:
        raise KeyError(
            f"未知后端: {backend_id}，可用: {list(registry.keys())}"
        )
    meta = registry[backend_id]
    module_name = meta.get("adapter", f"backends.{backend_id}")
    # 导入 adapter 模块（约定 backends/<id>/adapter.py 或 backends/<id>_adapter.py）
    import importlib
    try:
        mod = importlib.import_module(f"{module_name}.adapter")
    except ModuleNotFoundError:
        try:
            mod = importlib.import_module(f"backends.{backend_id}_adapter")
        except ModuleNotFoundError:
            raise RuntimeError(
                f"后端 {backend_id} 缺少 adapter 实现 "
                f"（期望 backends/{backend_id}/adapter.py）"
            )
    cls = getattr(mod, "Adapter")
    return cls(meta, server_url=server_url, config_dir=CONFIG_DIR,
               comfy_output=DEFAULT_COMFY_OUTPUT)


# ============ 通用基类 ============

class ComfyBackend:
    """所有后端基类：子类只需实现 build_workflow()"""

    backend_id = ""
    kind = ""           # t2v / i2v / ti2v / t2i
    capabilities = set()

    def __init__(self, meta: dict, server_url: str,
                 config_dir: Path, comfy_output: Path):
        self.meta = meta
        self.backend_id = meta.get("id", self.backend_id)
        self.kind = meta.get("kind", self.kind)
        self.capabilities = set(meta.get("capabilities", self.capabilities))
        self.defaults = meta.get("defaults", {})
        self.server_url = server_url.rstrip("/")
        self.config_dir = Path(config_dir)
        self.comfy_output = Path(comfy_output)
        self._run_id = uuid.uuid4().hex[:12]   # 本次运行的唯一标识

    # ---- 子类必须实现 ----

    def build_workflow(self, *, prompt: str, negative_prompt: str = "",
                       image=None, seed: int = 42, width: int = None,
                       height: int = None, length: int = None,
                       steps: int = None, cfg: float = None,
                       **kwargs) -> dict:
        """加载工作流模板并改写节点，返回可提交的 workflow dict。

        子类实现要点：
        - 用 self._load_workflow() 加载模板
        - 设置提示词 / seed / 分辨率 / 帧数 / 步数 / cfg
        - 如有 image 入参（I2V），先 self._upload_image() 再引用
        - 把输出节点 filename_prefix 设为 self._run_id（取回用）
        """
        raise NotImplementedError

    # ---- 基类通用能力 ----

    def _load_workflow(self) -> dict:
        """加载本后端工作流模板（config/<workflow>）"""
        wf_name = self.meta.get("workflow")
        if not wf_name:
            raise RuntimeError(f"后端 {self.backend_id} 未配置 workflow")
        wf_path = self.config_dir / wf_name
        if not wf_path.exists():
            raise RuntimeError(
                f"缺少工作流模板 {wf_name}（后端 {self.backend_id}）。\n"
                f"请参考 backends/README.md 在 ComfyUI 里导出 API 格式后保存到: {wf_path}"
            )
        return json.loads(wf_path.read_text(encoding="utf-8"))

    def _set_filename_prefix(self, workflow: dict, node_id: str = None):
        """把输出节点（SaveVideo/SaveImage）的 filename_prefix 设为本次 run_id"""
        for nid, node in workflow.items():
            inputs = node.get("inputs", {})
            cls = node.get("class_type", "")
            if node_id and nid != node_id:
                continue
            if "filename_prefix" in inputs and ("Save" in cls):
                inputs["filename_prefix"] = f"{self._run_id}"
        return workflow

    def _upload_image(self, image_path) -> str:
        """上传图片到 ComfyUI，返回服务器端文件名（含子目录）。"""
        import io
        img = Path(image_path)
        if not img.exists():
            raise FileNotFoundError(f"图片不存在: {img}")
        boundary = uuid.uuid4().hex
        data = []
        data.append(f"--{boundary}\r\n".encode())
        data.append(
            f'Content-Disposition: form-data; name="image"; filename="{img.name}"\r\n'.encode()
        )
        data.append(b"Content-Type: image/png\r\n\r\n")
        data.append(img.read_bytes())
        data.append(b"\r\n")
        data.append(f"--{boundary}--\r\n".encode())
        body = b"".join(data)
        result = json.loads(_request_retry(
            f"{self.server_url}/upload/image", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=60))
        name = result.get("name")
        if not name:
            raise RuntimeError(f"图片上传失败: {result}")
        # ComfyUI 会把上传图放进 input/ 目录，LoadImage 用相对名引用
        return name

    def submit(self, workflow: dict) -> str:
        """提交工作流，返回 prompt_id（带自动重试）"""
        data = json.dumps({"prompt": workflow}).encode()
        body = _request_retry(
            f"{self.server_url}/prompt", data=data,
            headers={"Content-Type": "application/json"})
        return json.loads(body.decode())["prompt_id"]

    def wait(self, prompt_id: str, timeout_s: int = 3600) -> dict:
        """轮询 ComfyUI /history/<prompt_id> 等待任务完成。

        用 HTTP 轮询而不是 websocket：ComfyUI 0.30 的 websocket 完成消息
        顺序不保证（execution_error 与 executing(node=None) 可能乱序），
        而 /history 的 status 字段是权威状态。
        返回 {'status': 'success'|'error', ...}
        """
        deadline = time.time() + timeout_s
        last_status = None
        while time.time() < deadline:
            try:
                req = urllib.request.Request(
                    f"{self.server_url}/history/{prompt_id}",
                    headers={"Accept": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode())
                entry = data.get(prompt_id)
                if entry:
                    status = entry.get("status", {})
                    last_status = status.get("status_str")
                    if status.get("completed") or status.get("status_str") == "success":
                        return {"status": "success"}
                    if status.get("status_str") == "error":
                        return {"status": "error", "error": entry}
            except urllib.error.HTTPError as e:
                # 404 = 任务还未进入 history；5xx = 服务端瞬时错误，都继续等
                if 400 <= e.code < 500 and e.code != 404:
                    return {"status": "error", "error": f"HTTP {e.code}"}
            except Exception:
                pass
            time.sleep(2)
        return {"status": "error",
                "error": f"timeout after {timeout_s}s (last status: {last_status})"}

    def fetch_output(self, suffix: str = None) -> Path:
        """按本次 run_id 从 ComfyUI 输出目录取回最新文件。

        优先按 filename_prefix 精确匹配（uuid 前缀），找不到再退回 mtime 兜底。
        """
        suffix = suffix or self.defaults.get("output_suffix", ".mp4")
        # 1) 精确匹配：文件名以 run_id 开头
        exact = sorted(
            self.comfy_output.glob(f"{self._run_id}*{suffix}"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if exact:
            return exact[0]
        # 2) 兜底：同后缀最新文件（兼容旧模板未设 prefix 的情况）
        fallback = sorted(
            self.comfy_output.glob(f"*{suffix}"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if fallback:
            return fallback[0]
        raise RuntimeError(
            f"后端 {self.backend_id} 输出目录没有找到文件 "
            f"（run_id={self._run_id}，后缀={suffix}）"
        )

    # ---- 便捷方法 ----

    def supports(self, capability: str) -> bool:
        """后端是否支持某能力（t2i / t2v / i2v / audio ...）。
        与 registry.json 的 capabilities 字段对应，调度层用它校验任务可行性。
        """
        return capability in self.capabilities

    def resolve(self, key: str, default=None):
        """优先取调用方显式参数，其次后端默认参数，最后兜底"""
        return default
