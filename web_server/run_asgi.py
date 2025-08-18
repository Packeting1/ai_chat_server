import os
import subprocess
import sys

import django


def main():
    """启动ASGI服务器"""
    django.setup()

    # 从数据库获取配置
    try:
        from app.models import SystemConfig

        config = SystemConfig.get_config()
        host = config.web_host
        http_port = str(config.web_http_port)
        https_port = str(config.web_https_port)
        ssl_enabled = config.web_ssl_enabled
        ssl_cert_path = (
            config.web_ssl_cert_file.path if config.web_ssl_cert_file else ""
        )
        ssl_key_path = config.web_ssl_key_file.path if config.web_ssl_key_file else ""
    except Exception as e:
        print(f"⚠️  无法从数据库读取配置，使用默认值: {e}")
        host = "0.0.0.0"
        http_port = "8000"
        https_port = "8443"
        ssl_enabled = False
        ssl_cert_path = ""
        ssl_key_path = ""

    print("=" * 50)

    # 显示可用的协议和端口
    if ssl_enabled:
        print(f"🌐 Web界面: http://{host}:{http_port}")
        print(f"🔌 WebSocket: ws://{host}:{http_port}/ws/")
        print(f"🔧 管理后台: http://{host}:{http_port}/admin/")
        print("🔧 默认管理后台账号: admin admin")
        print(f"📡 监听地址: {host} - HTTP:{http_port}, HTTPS:{https_port}")
        print("🔐 协议模式: 🔒 HTTP + HTTPS")
    else:
        print(f"🌐 Web界面: http://{host}:{http_port}")
        print(f"🔌 WebSocket: ws://{host}:{http_port}/ws/")
        print(f"🔧 管理后台: http://{host}:{http_port}/admin/")
        print("🔧 默认管理后台账号: admin admin")
        print(f"📡 监听地址: {host}:{http_port}")
        print("🔐 协议模式: 🔓 仅HTTP")

    if ssl_enabled:
        print(f"📜 SSL证书: {ssl_cert_path}")
        print(f"🔑 SSL私钥: {ssl_key_path}")

    print("=" * 50)
    print("⏹️  按 Ctrl+C 退出")
    print("=" * 50)

    try:
        # 首先尝试使用daphne
        cmd = [sys.executable, "-m", "daphne", "-b", host]

        # 配置端口
        if ssl_enabled:
            if not ssl_cert_path or not ssl_key_path:
                print("❌ SSL已启用但证书或私钥路径为空！")
                print("💡 请在Django管理后台的系统配置中设置SSL证书和私钥路径")
                sys.exit(1)

            # 验证SSL文件是否存在
            if not os.path.exists(ssl_cert_path):
                print(f"❌ SSL证书文件不存在: {ssl_cert_path}")
                sys.exit(1)

            if not os.path.exists(ssl_key_path):
                print(f"❌ SSL私钥文件不存在: {ssl_key_path}")
                sys.exit(1)

            # 配置HTTP和HTTPS双端口
            cmd.extend(
                [
                    "-p",
                    http_port,  # HTTP端口
                    "-e",
                    f"ssl:{https_port}:privateKey={ssl_key_path}:certKey={ssl_cert_path}",  # HTTPS端口
                ]
            )
        else:
            # 只配置HTTP端点
            cmd.extend(["-p", http_port])

        cmd.append("ai_chat_server.asgi:application")

        print(f"🚀 启动命令: {' '.join(cmd)}")
        subprocess.run(cmd)

    except FileNotFoundError:
        print("❌ Daphne未安装，尝试使用uvicorn...")
        try:
            # 备选方案：使用uvicorn（只能支持单端口）
            if ssl_enabled:
                print("⚠️ Uvicorn不支持同时监听HTTP和HTTPS，将只启用HTTPS")
                port_to_use = https_port
            else:
                port_to_use = http_port

            cmd = [
                sys.executable,
                "-m",
                "uvicorn",
                "ai_chat_server.asgi:application",
                "--host",
                host,
                "--port",
                port_to_use,
                "--reload",
            ]

            # 如果启用SSL，添加SSL参数
            if ssl_enabled:
                if not ssl_cert_path or not ssl_key_path:
                    print("❌ SSL已启用但证书或私钥路径为空！")
                    print("💡 请在Django管理后台的系统配置中设置SSL证书和私钥路径")
                    sys.exit(1)

                # 验证SSL文件是否存在
                if not os.path.exists(ssl_cert_path):
                    print(f"❌ SSL证书文件不存在: {ssl_cert_path}")
                    sys.exit(1)

                if not os.path.exists(ssl_key_path):
                    print(f"❌ SSL私钥文件不存在: {ssl_key_path}")
                    sys.exit(1)

                # Uvicorn SSL配置
                cmd.extend(
                    ["--ssl-keyfile", ssl_key_path, "--ssl-certfile", ssl_cert_path]
                )

            print(f"🚀 启动命令: {' '.join(cmd)}")
            subprocess.run(cmd)

        except FileNotFoundError:
            print("❌ 未找到ASGI服务器！")
            print("📋 请安装其中一个：")
            print("   pip install daphne")
            print("   或")
            print("   pip install uvicorn")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n👋 ASGI服务器已关闭")
    except Exception as e:
        print(f"\n❌ 启动失败: {e}")


if __name__ == "__main__":
    main()
