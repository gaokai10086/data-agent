"""
================================================================================
脚本名称：问数平台全栈一键启动器 (带端口冲突自动清理) 🚀
脚本路径：start_all.py
================================================================================
"""
import atexit

import signal
import subprocess
import sys
import time

processes: list[subprocess.Popen] = []


def free_port(port: int):
    """如果端口被残留进程占用，自动强制杀掉释放"""
    try:
        # lsof -ti :port 可以直接拿到占用该端口的所有 PID
        res = subprocess.run(["lsof", "-ti", f":{port}"], capture_output=True, text=True)
        pids = res.stdout.strip().split()
        if pids:
            print(f"⚠️ 检测到端口 {port} 被旧进程占用 (PID: {pids})，正在自动释放...")
            for pid in pids:
                subprocess.run(["kill", "-9", pid], capture_output=True)
            time.sleep(1)
            print(f"✅ 端口 {port} 已成功清理！")
    except Exception as e:
        # 非 Unix 系统或命令未找到时跳过
        pass


def terminate_all_processes():
    """清理并安全杀死所有子进程"""
    for proc in processes:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()


def signal_handler(sig, frame):
    terminate_all_processes()
    sys.exit(0)


def main():
    atexit.register(terminate_all_processes)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 1. 启动前主动扫描并清理 8000 和 8501 端口，防止端口冲突
    free_port(8000)
    free_port(8501)

    python_executable = sys.executable

    # 2. 启动后端 FastAPI (Uvicorn)
    backend_cmd = [
        python_executable,
        "-m", "uvicorn",
        "main:app",
        "--host", "0.0.0.0",
        "--port", "8000",
        "--reload",
        "--log-level", "warning",
    ]
    backend_proc = subprocess.Popen(
        backend_cmd,
        stdout=sys.stdout,
        stderr=sys.stderr,
        shell=False
    )
    processes.append(backend_proc)
    print("后端服务已启动，URL: http://localhost:8000")

    time.sleep(2)

    # 3. 启动前端 Web UI (Streamlit)
    frontend_cmd = [
        python_executable,
        "-m", "streamlit",
        "run",
        "web_ui.py",
        "--server.port", "8501",
        "--server.address", "0.0.0.0",
        "--logger.level", "error",
    ]
    frontend_proc = subprocess.Popen(
        frontend_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False
    )
    processes.append(frontend_proc)
    print("前端服务已启动，URL: http://localhost:8501")
    print("前端服务已启动，Network URL: http://172.16.104.27:8501")
    print("运行时环境已加载（MySQL、Elasticsearch、Qdrant、Embedding）")
    print("AI Agentic BI 智能问数平台已启动，访问地址: http://localhost:8501")

    while True:
        for proc in processes:
            return_code = proc.poll()
            if return_code is not None:
                print(f"⚠️ 检测到子进程意外退出，退出码: {return_code}")
                terminate_all_processes()
                sys.exit(return_code)
        time.sleep(1)


if __name__ == '__main__':
    main()
