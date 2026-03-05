if __name__ == "__main__":
    print("本地单终端模式已移除。")
    print("请使用:")
    print("  uv run survival-host --name 房主A --bind 0.0.0.0 --port 9009")
    print("  uv run survival-client --host <房主IP> --port 9009 --room <房间号> --name 玩家B")
