def main() -> int:
    try:
        import adk  # noqa: F401
    except Exception as e:
        print("adk import failed:", e)
        return 1
    print("adk import: OK")

    try:
        import adk_a2a_settlement  # noqa: F401
    except Exception as e:
        print("adk_a2a_settlement import failed:", e)
        return 1
    print(f"adk_a2a_settlement import: OK (v{adk_a2a_settlement.__version__})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
