from .interfaces import ResponseMessage

ERROR_CODE_MAPPING = {
    -1: ("内部错误",
         "内部错误"),
    100001: ("服务并发度超过上限，消息请求失败",
             "服务并发度超过上限，消息请求失败"),
    100002: ("服务启动失败",
             "服务启动失败"),
}


def exception_message(code: int, language: str = "cn") -> ResponseMessage:
    if code not in ERROR_CODE_MAPPING:
        code = -1
    if language == "cn":
        return ResponseMessage(code, ERROR_CODE_MAPPING[code][0])
    else:
        return ResponseMessage(code, ERROR_CODE_MAPPING[code][1])
