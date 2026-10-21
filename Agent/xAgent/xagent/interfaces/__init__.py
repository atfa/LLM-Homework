"""接口层：所有 UI 适配器。

新增界面时在 ``interfaces/`` 下加子包（如 ``web/``、``desktop/``），
复用 ``application`` 的 :class:`AgentService` 与事件流即可，
禁止把 UI 逻辑写进 core。
"""
