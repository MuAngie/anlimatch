"""Shared model client for needs extraction, combination generation and evaluation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
import time
from model_usage import record, usage_fields
from task_runtime import progress, observer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent


class ModelClient:
    def __init__(self):
        # One configuration snapshot is used for both calls in a request.
        values = {}
        env_file = ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8-sig").splitlines():
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                key, separator, value = line.partition("=")
                if not separator or key.strip() not in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL"):
                    raise RuntimeError(".env 格式不正确，仅配置 OPENAI_BASE_URL、OPENAI_API_KEY、OPENAI_MODEL。")
                values[key.strip()] = value.strip().strip('"').strip("'")
        keys = ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL")
        values = {key: os.environ.get(key, values.get(key, "")).strip() for key in keys}
        if any(values.values()):
            if not all(values.values()):
                raise RuntimeError("API 配置不完整，请在 .env 中填写 API 地址、密钥和模型名称。")
            self.source = "模型 API"
            self.model = values["OPENAI_MODEL"]
            self.base_url, self.key = values["OPENAI_BASE_URL"].rstrip("/"), values["OPENAI_API_KEY"]
            from urllib.parse import urlparse
            address = urlparse(self.base_url)
            if address.scheme != "https" and not (address.scheme == "http" and address.hostname in ("127.0.0.1", "localhost")):
                raise RuntimeError("API 地址须使用 HTTPS（本机服务可使用 HTTP）。")
            self.runtime = None
        else:
            self.source = "Codex CLI"
            self.runtime = codex_runtime()
            self.model = self.runtime[1] or "Codex 默认模型"

    def call(self, prompt, data, schema_name):
        started = time.monotonic()
        self._usage = {}
        self._failure = None
        status, error = 'error', None
        try:
            result = self._call(prompt, data, schema_name)
            status = 'ok'
            return result
        except Exception as exc:
            error = self._failure or type(exc).__name__
            raise
        finally:
            record(dict(stage=schema_name.removesuffix('_schema.json'),model=self.model,source=self.source,
                        status=status,error=error,elapsed_ms=round((time.monotonic()-started)*1000),
                        input_chars=len(prompt)+len(json.dumps(data,ensure_ascii=False,separators=(',',':'))),
                        **usage_fields(self._usage)))

    def _call(self, prompt, data, schema_name):
        progress({'needs_schema.json':'extract','matching_schema.json':'select',
                  'compose_schema.json':'compose','eval_schema.json':'review'}.get(schema_name,'model'))
        schema_path = ROOT / schema_name
        if self.runtime:
            matching = schema_name == "matching_schema.json"
            return run_codex(data, prompt, schema_path, self.runtime, timeout=300 if matching else 180,
                             effort_override="medium" if matching else None, usage_callback=self._usage.update)
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        body = {"model": self.model, "messages": [{"role": "system", "content": prompt},
                 {"role": "user", "content": json.dumps(data, ensure_ascii=False,separators=(',',':'))}],
                "response_format": {"type": "json_schema", "json_schema": {"name": schema_path.stem,
                                    "strict": True, "schema": schema}}}
        request = Request(self.base_url + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.key})
        try:
            with urlopen(request, timeout=300 if schema_name == "matching_schema.json" else 180) as result:
                value = json.load(result)
            self._usage = value.get('usage') or {}
            progress()
            choice = value["choices"][0]
            message = choice["message"]
            if choice.get("finish_reason") != "stop" or message.get("refusal") or message.get("tool_calls"):
                raise ValueError("模型结果未完成")
            return json.loads(message["content"]), self.model
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, IndexError, TypeError) as exc:
            self._failure = type(exc).__name__ + (':'+str(exc.code) if isinstance(exc, HTTPError) else '')
            raise RuntimeError("模型 API 调用失败或结果无效，请检查 API 配置、网络、额度及结构化输出支持。") from None


def codex_runtime():
    config_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    config_path = config_home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    # Call the native executable, avoiding shell interpolation and wrapper child processes.
    command = shutil.which("codex.exe")
    if not command and os.name == "nt":
        base = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@openai/codex"
        command = next(iter(base.glob("**/vendor/*/bin/codex.exe")), None)
    if not command and os.name != "nt":
        command = shutil.which("codex")
    if not command:
        raise RuntimeError("未找到 Codex CLI。请安装并使用 codex login 登录后重试。")
    return str(command), config.get("model"), config.get("model_reasoning_effort")


def run_codex(data, prompt, schema_path, runtime, timeout=180, effort_override=None, usage_callback=None):
    executable, model, effort = runtime
    effort = effort_override or effort
    args = [executable, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
            "--sandbox", "read-only", "--json", "--output-schema", str(schema_path)]
    if model:
        args += ["--model", model]
    if effort:
        args += ["-c", 'model_reasoning_effort=' + json.dumps(effort)]
    args += ["-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
             "-c", "project_doc_max_bytes=0", "-c", 'history.persistence="none"']
    for feature in ("shell_tool", "unified_exec", "apps", "plugins", "hooks", "multi_agent", "browser_use",
                    "computer_use", "image_generation", "view_image", "skill_search", "skill_mcp_dependency_install",
                    "workspace_dependencies", "memories", "sleep_tool", "goals"):
        args += ["--disable", feature]
    args += ["-"]
    with tempfile.TemporaryDirectory(prefix="health-eval-") as workdir:
        options = dict(text=True, encoding='utf-8', errors='replace', cwd=workdir,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        content = prompt + json.dumps(data, ensure_ascii=False,separators=(',',':'))
        if observer.get() is None:
            result = subprocess.run(args,input=content,capture_output=True,timeout=timeout,**options)
        else:
            import time
            process = subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**options)
            started = time.monotonic()
            try:
                while True:
                    progress()
                    if time.monotonic()-started > timeout: raise subprocess.TimeoutExpired(args,timeout)
                    try:
                        stdout, stderr = process.communicate(input=content,timeout=0.5)
                        result = subprocess.CompletedProcess(args,process.returncode,stdout,stderr)
                        break
                    except subprocess.TimeoutExpired:
                        content = None
            finally:
                if process.poll() is None: process.kill()
                process.communicate()
            progress()
    if result.returncode:
        raise RuntimeError("Codex 模型调用失败，请检查本机登录状态、网络或账号额度后重试。")
    final = None
    for line in result.stdout.splitlines():
        event = json.loads(line)
        if event.get('type') == 'turn.completed' and usage_callback:
            usage_callback(event.get('usage') or {})
        if event.get("type") == "turn.failed" or event.get("type") == "error":
            raise RuntimeError("Codex 模型未完成，请检查网络或账号额度后重试。")
        if event.get("type") in ("item.started", "item.completed"):
            item = event.get("item", {})
            if item.get("type") not in ("reasoning", "agent_message"):
                raise RuntimeError("模型发生非预期工具调用，结果已拦截。")
            if event["type"] == "item.completed" and item.get("type") == "agent_message":
                final = json.loads(item["text"])
    if final is None:
        raise RuntimeError("未收到大模型结果，请重试。")
    return final, model or "Codex 默认模型"


