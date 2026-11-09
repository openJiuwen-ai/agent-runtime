"""企业版 Skill 按包上传审批：领域异常。"""

from __future__ import annotations


class SkillUploadError(Exception):
    """带 HTTP 语义的上传审批错误。"""

    status_code = 400
    code = "SKILL_UPLOAD_INVALID"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class SkillUploadValidationError(SkillUploadError):
    status_code = 400
    code = "SKILL_UPLOAD_INVALID"


class SkillUploadForbiddenError(SkillUploadError):
    status_code = 403
    code = "SKILL_UPLOAD_FORBIDDEN"


class SkillUploadNotFoundError(SkillUploadError):
    status_code = 404
    code = "SKILL_UPLOAD_NOT_FOUND"


class SkillUploadConflictError(SkillUploadError):
    status_code = 409
    code = "SKILL_UPLOAD_CONFLICT"


class SkillUploadExpiredError(SkillUploadError):
    """审批单已失效，或暂存包已超过保留期。"""

    status_code = 410
    code = "SKILL_UPLOAD_PACKAGE_EXPIRED"


class SkillUploadPackageInvalidError(SkillUploadError):
    """原始包无法安全解包（损坏、缺 SKILL.md、路径非法等）。"""

    status_code = 400
    code = "SKILL_UPLOAD_PACKAGE_INVALID"


class SkillUploadPackageUnavailableError(SkillUploadError):
    """对象存储不可达，或返回内容与声明的摘要不一致。"""

    status_code = 502
    code = "SKILL_UPLOAD_PACKAGE_UNAVAILABLE"


class SkillUploadUnavailableError(SkillUploadError):
    status_code = 503
    code = "SKILL_UPLOAD_UNAVAILABLE"
