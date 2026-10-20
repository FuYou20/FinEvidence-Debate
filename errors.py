class BaseAppError(Exception):
    """应用内部异常的基类。"""


class LLMAPIError(BaseAppError):
    """调用模型 API 时发生的异常。"""

    def __init__(self, message, *, retryable=False, status_code=None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class RequestError(BaseAppError):
    """发起联网请求时发生的异常。"""

    def __init__(self, message, *, retryable=False, status_code=None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class LLMStreamError(LLMAPIError):
    """读取模型流式响应时发生的异常。"""


class LLMJsonParseError(BaseAppError):
    """模型返回的 JSON 解析异常。"""


class ToolError(BaseAppError):
    """工具执行过程中发生的业务异常。"""

    def __init__(
        self,
        message,
        *,
        tool_name=None,
        code="TOOL_EXECUTION_ERROR",
        retryable=False,
    ):
        super().__init__(message)
        self.tool_name = tool_name
        self.code = code
        self.retryable = retryable


class AgentLoopError(BaseAppError):
    """Agent 编排过程异常。"""


class ReadPdfError(BaseAppError):
    """读取 PDF 异常。"""


class RAGError(BaseAppError):
    """RAG Pipeline 中可预期的业务异常。"""


class EmptyDocumentError(RAGError):
    """文档没有产生任何可用于建立索引的文本块。"""


class QueryEmbeddingError(RAGError):
    """查询文本的向量化结果不符合检索协议。"""
    
    
class InvalidRetrievalResultError(RAGError):
    """验证查询返回结果失败。"""


class InvalidCitationError(RAGError):
    """模型返回的证据引用验证失败。"""
