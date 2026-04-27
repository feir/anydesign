## Known Pitfalls
- **CLI timeout 参数必须透传每个 I/O 操作调用层**：BrowserSession.screenshot() 的 timeout_s 硬编码在 call site（timeout_s=30），而非作为函数签名参数暴露。CLI --timeout 90 在 _extract_with_session → screenshot() 调用链中被丢弃，用户传递的超时值静默失效。防错：I/O 操作函数（截图、网络请求）的超时参数必须作为函数签名命名参数暴露；添加 CLI 参数后立即 grep 调用链所有层确认每层都透传
