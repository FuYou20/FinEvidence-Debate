from pydantic import ValidationError

from errors import LLMJsonParseError
from .investigation_schema import EvidenceDecision


def _normalize_required_string(value, *, field_name):
    """校验并规范化模型调用所需的非空字符串。"""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是字符串")

    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{field_name} 不能为空")

    return normalized_value


def _build_prompt_input(*, system_prompt, user_prompt):
    """构造 Responses API 使用的系统和用户消息。"""
    normalized_system_prompt = _normalize_required_string(
        system_prompt,
        field_name="system_prompt",
    )
    normalized_user_prompt = _normalize_required_string(
        user_prompt,
        field_name="user_prompt",
    )

    return [
        {
            "role": "system",
            "content": normalized_system_prompt,
        },
        {
            "role": "user",
            "content": normalized_user_prompt,
        },
    ]


def create_text_generator(client, *, model_name):
    """创建返回普通文本的模型生成函数。"""
    normalized_model_name = _normalize_required_string(
        model_name,
        field_name="model_name",
    )

    def generate_answer(*, system_prompt, user_prompt):
        response = client.responses.create(
            model=normalized_model_name,
            input=_build_prompt_input(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            ),
        )
        return response.output_text

    return generate_answer


def create_evidence_decision_generator(client, *, model_name):
    """创建返回 ``EvidenceDecision`` 的结构化证据判断函数。"""
    normalized_model_name = _normalize_required_string(
        model_name,
        field_name="model_name",
    )

    def generate_decision(*, system_prompt, user_prompt):
        response = client.responses.parse(
            model=normalized_model_name,
            input=_build_prompt_input(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            ),
            text_format=EvidenceDecision,
        )

        parsed_decision = response.output_parsed
        if parsed_decision is None:
            raise LLMJsonParseError(
                "模型未返回可解析的 EvidenceDecision 结构化结果"
            )

        try:
            return EvidenceDecision.model_validate(parsed_decision)
        except ValidationError as exc:
            raise LLMJsonParseError(
                "模型返回结果不符合 EvidenceDecision 协议"
            ) from exc

    return generate_decision
