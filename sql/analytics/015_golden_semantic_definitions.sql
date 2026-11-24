BEGIN;
ALTER TABLE analytics.scenario ADD COLUMN IF NOT EXISTS semantic_json jsonb NOT NULL DEFAULT '{}'::jsonb;
UPDATE analytics.scenario SET semantic_json = $json${
  "version": 1,
  "answers": {
    "fact": {
      "fact_id": "fact_agent_turn",
      "name": "AgentTurnFact",
      "grain": "session_id + message_id",
      "description": "Agent 轮次事实：一次用户请求/智能体轮次一行。"
    },
    "labels": {
      "session_id": "会话标识",
      "turn_id": "轮次标识",
      "agent_name": "Agent 名称",
      "model_name": "模型名称",
      "event_time": "事件时间",
      "total_tokens": "总 Token",
      "cached_tokens": "缓存 Token",
      "input_prompt": "输入提示词",
      "turn_count": "轮次数",
      "input_tokens": "输入 Token",
      "output_tokens": "输出 Token",
      "latency_ms": "轮次耗时",
      "avg_tokens_per_turn": "轮次平均 Token"
    },
    "metrics": {
      "turn_count": {
        "code": "turn_count",
        "unit": "次",
        "description": "按轮次计数；一行一个轮次。",
        "data_type": "integer",
        "aggregation": "count",
        "additivity": "additive",
        "filter_expression": "",
        "format": "#,##0",
        "sort_order": 10
      },
      "input_tokens": {
        "code": "input_tokens",
        "unit": "token",
        "description": "输入的 token 数；可加。",
        "data_type": "integer",
        "aggregation": "sum",
        "additivity": "additive",
        "filter_expression": "",
        "format": "#,##0",
        "sort_order": 20
      },
      "output_tokens": {
        "code": "output_tokens",
        "unit": "token",
        "description": "输出的 token 数；可加。",
        "data_type": "integer",
        "aggregation": "sum",
        "additivity": "additive",
        "filter_expression": "",
        "format": "#,##0",
        "sort_order": 30
      },
      "total_tokens_sum": {
        "code": "total_tokens",
        "unit": "token",
        "description": "usage.total_tokens 求和。",
        "data_type": "bigint",
        "aggregation": "sum",
        "additivity": "additive",
        "filter_expression": "",
        "format": "#,##0",
        "sort_order": 50
      },
      "cached_tokens_sum": {
        "code": "cached_tokens",
        "unit": "token",
        "description": "usage.cached_tokens 求和。",
        "data_type": "bigint",
        "aggregation": "sum",
        "additivity": "additive",
        "filter_expression": "",
        "format": "#,##0",
        "sort_order": 60
      },
      "latency_ms": {
        "code": "latency_ms",
        "unit": "ms",
        "description": "轮次端到端耗时；不可加，按 avg 计算。",
        "data_type": "numeric",
        "aggregation": "avg",
        "additivity": "non_additive",
        "filter_expression": "",
        "format": "#,##0",
        "sort_order": 40
      },
      "avg_tokens_per_turn": {
        "code": "avg_tokens_per_turn",
        "unit": "token",
        "description": "每轮输入+输出的平均值；依赖两个基础指标。",
        "expression_language": "pseudo",
        "expression": "avg(prompt_tokens + completion_tokens)",
        "format": "#,##0.0",
        "sort_order": 10,
        "dependencies": [
          "bm_turn_input_tokens",
          "bm_turn_output_tokens"
        ]
      }
    },
    "fields": {
      "session_id": {
        "data_type": "text",
        "role": "key",
        "description": "轮次所属会话。",
        "is_enum": false,
        "sort_order": 10
      },
      "message_id": {
        "data_type": "text",
        "role": "key",
        "description": "轮次唯一标识。",
        "is_enum": false,
        "sort_order": 20
      },
      "agent_name": {
        "data_type": "text",
        "role": "display",
        "description": "业务智能体名称。",
        "is_enum": false,
        "sort_order": 30
      },
      "model_id": {
        "data_type": "text",
        "role": "display",
        "description": "本轮使用的模型。",
        "is_enum": false,
        "sort_order": 40
      },
      "event_time": {
        "data_type": "timestamptz",
        "role": "display",
        "description": "assistant 消息时间（含时区）。",
        "is_enum": false,
        "sort_order": 70
      },
      "total_tokens": {
        "data_type": "bigint",
        "role": "display",
        "description": "usage.total_tokens；缓存命中也会反映。",
        "is_enum": false,
        "sort_order": 80
      },
      "cached_tokens": {
        "data_type": "bigint",
        "role": "display",
        "description": "usage.cached_tokens；用于评估缓存收益。",
        "is_enum": false,
        "sort_order": 90
      },
      "input_prompt": {
        "data_type": "text",
        "role": "display",
        "description": "本轮 assistant 之前最近一条 user 消息的正文。",
        "is_enum": false,
        "sort_order": 65
      }
    },
    "enums": {}
  },
  "definitions": {
    "fact": {
      "fact_id": "fact_agent_turn",
      "name": "AgentTurnFact",
      "grain": "session_id + message_id",
      "description": "Agent 轮次事实：一次用户请求/智能体轮次一行。"
    },
    "columns": [
      {
        "alias": "session_id",
        "kind": "fact_field",
        "code": "session_id",
        "name": "会话标识",
        "description": "轮次所属会话。",
        "data_type": "text",
        "role": "key",
        "is_enum": false,
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "session_id"
        }
      },
      {
        "alias": "turn_id",
        "kind": "fact_field",
        "code": "turn_id",
        "name": "轮次标识",
        "description": "轮次唯一标识。",
        "data_type": "text",
        "role": "key",
        "is_enum": false,
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "message_id"
        }
      },
      {
        "alias": "agent_name",
        "kind": "fact_field",
        "code": "agent_name",
        "name": "Agent 名称",
        "description": "业务智能体名称。",
        "data_type": "text",
        "role": "display",
        "is_enum": false,
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "agent_name"
        }
      },
      {
        "alias": "model_name",
        "kind": "fact_field",
        "code": "model_name",
        "name": "模型名称",
        "description": "本轮使用的模型。",
        "data_type": "text",
        "role": "display",
        "is_enum": false,
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "model_id"
        }
      },
      {
        "alias": "event_time",
        "kind": "fact_field",
        "code": "event_time",
        "name": "事件时间",
        "description": "assistant 消息时间（含时区）。",
        "data_type": "timestamptz",
        "role": "display",
        "is_enum": false,
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "event_time"
        }
      },
      {
        "alias": "total_tokens",
        "kind": "fact_field",
        "code": "total_tokens",
        "name": "总 Token",
        "description": "usage.total_tokens；缓存命中也会反映。",
        "data_type": "bigint",
        "role": "display",
        "is_enum": false,
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "total_tokens"
        }
      },
      {
        "alias": "cached_tokens",
        "kind": "fact_field",
        "code": "cached_tokens",
        "name": "缓存 Token",
        "description": "usage.cached_tokens；用于评估缓存收益。",
        "data_type": "bigint",
        "role": "display",
        "is_enum": false,
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "cached_tokens"
        }
      },
      {
        "alias": "input_prompt",
        "kind": "fact_field",
        "code": "input_prompt",
        "name": "输入提示词",
        "description": "本轮 assistant 之前最近一条 user 消息的正文。",
        "data_type": "text",
        "role": "display",
        "is_enum": false,
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "input_prompt"
        }
      },
      {
        "alias": "turn_count",
        "kind": "base_metric",
        "code": "turn_count",
        "name": "轮次数",
        "description": "按轮次计数；一行一个轮次。",
        "data_type": "integer",
        "unit": "次",
        "aggregation": "count",
        "additivity": "additive",
        "filter_expression": "",
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "message_id"
        }
      },
      {
        "alias": "input_tokens",
        "kind": "base_metric",
        "code": "input_tokens",
        "name": "输入 Token",
        "description": "输入的 token 数；可加。",
        "data_type": "integer",
        "unit": "token",
        "aggregation": "sum",
        "additivity": "additive",
        "filter_expression": "",
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "prompt_tokens"
        }
      },
      {
        "alias": "output_tokens",
        "kind": "base_metric",
        "code": "output_tokens",
        "name": "输出 Token",
        "description": "输出的 token 数；可加。",
        "data_type": "integer",
        "unit": "token",
        "aggregation": "sum",
        "additivity": "additive",
        "filter_expression": "",
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "completion_tokens"
        }
      },
      {
        "alias": "total_tokens_sum",
        "kind": "base_metric",
        "code": "total_tokens",
        "name": "总 Token",
        "description": "usage.total_tokens 求和。",
        "data_type": "bigint",
        "unit": "token",
        "aggregation": "sum",
        "additivity": "additive",
        "filter_expression": "",
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "total_tokens"
        }
      },
      {
        "alias": "cached_tokens_sum",
        "kind": "base_metric",
        "code": "cached_tokens",
        "name": "缓存 Token",
        "description": "usage.cached_tokens 求和。",
        "data_type": "bigint",
        "unit": "token",
        "aggregation": "sum",
        "additivity": "additive",
        "filter_expression": "",
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "cached_tokens"
        }
      },
      {
        "alias": "latency_ms",
        "kind": "base_metric",
        "code": "latency_ms",
        "name": "轮次耗时",
        "description": "轮次端到端耗时；不可加，按 avg 计算。",
        "data_type": "numeric",
        "unit": "ms",
        "aggregation": "avg",
        "additivity": "non_additive",
        "filter_expression": "",
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "agent_duration_ms"
        }
      },
      {
        "alias": "avg_tokens_per_turn",
        "kind": "derived_metric",
        "code": "avg_tokens_per_turn",
        "name": "轮次平均 Token",
        "description": "每轮输入+输出的平均值；依赖两个基础指标。",
        "expression_language": "pseudo",
        "expression": "avg(prompt_tokens + completion_tokens)",
        "unit": "token",
        "format": "#,##0.0",
        "sort_order": 10,
        "dependencies": [
          "bm_turn_input_tokens",
          "bm_turn_output_tokens"
        ]
      }
    ],
    "dimensions": [
      {
        "dimension_id": "dim_agent",
        "key_code": "agent_name",
        "role": "group_by",
        "fact_key": "agent_name",
        "dimension_key": "agent_name",
        "join_type": "none"
      },
      {
        "dimension_id": "dim_model",
        "key_code": "model_id",
        "role": "group_by",
        "fact_key": "model_id",
        "dimension_key": "model_id",
        "join_type": "none"
      },
      {
        "dimension_id": "dim_session",
        "key_code": "session_id",
        "role": "group_by",
        "fact_key": "session_id",
        "dimension_key": "session_id",
        "join_type": "none"
      },
      {
        "dimension_id": "dim_time",
        "key_code": "event_time",
        "role": "group_by",
        "fact_key": "event_time",
        "dimension_key": "event_time",
        "join_type": "none"
      }
    ],
    "mappings": [
      {
        "semantic_kind": "non_metric_field",
        "semantic_id": "ff_turn_session_id",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "session_id"
      },
      {
        "semantic_kind": "non_metric_field",
        "semantic_id": "ff_turn_turn_id",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "message_id"
      },
      {
        "semantic_kind": "non_metric_field",
        "semantic_id": "ff_turn_agent_name",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "agent_name"
      },
      {
        "semantic_kind": "non_metric_field",
        "semantic_id": "ff_turn_model_name",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "model_id"
      },
      {
        "semantic_kind": "non_metric_field",
        "semantic_id": "ff_turn_input_prompt",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "input_prompt"
      },
      {
        "semantic_kind": "base_metric",
        "semantic_id": "bm_agent_turn_count",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "message_id"
      },
      {
        "semantic_kind": "base_metric",
        "semantic_id": "bm_turn_input_tokens",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "prompt_tokens"
      },
      {
        "semantic_kind": "base_metric",
        "semantic_id": "bm_turn_output_tokens",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "completion_tokens"
      },
      {
        "semantic_kind": "base_metric",
        "semantic_id": "bm_turn_latency_ms",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "agent_duration_ms"
      },
      {
        "semantic_kind": "non_metric_field",
        "semantic_id": "ff_turn_event_time",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "event_time"
      },
      {
        "semantic_kind": "non_metric_field",
        "semantic_id": "ff_turn_total_tokens",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "total_tokens"
      },
      {
        "semantic_kind": "non_metric_field",
        "semantic_id": "ff_turn_cached_tokens",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "cached_tokens"
      },
      {
        "semantic_kind": "base_metric",
        "semantic_id": "bm_turn_total_tokens",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "total_tokens"
      },
      {
        "semantic_kind": "base_metric",
        "semantic_id": "bm_turn_cached_tokens",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "cached_tokens"
      }
    ]
  }
}$json$::jsonb WHERE scenario_id = 'sc_semantic_agent_turn_full';
UPDATE analytics.scenario SET semantic_json = $json${
  "version": 1,
  "answers": {
    "fact": {
      "fact_id": "fact_agent_tool_context",
      "name": "AgentToolContextFact",
      "grain": "session_id + message_id + tool_call_id",
      "description": "工具调用事实 + 所属轮次上下文；一次工具调用一行，轮次字段来自同一条 assistant 消息。"
    },
    "labels": {
      "tool_code": "工具编码",
      "input_prompt": "输入提示词",
      "tool_call_count": "工具调用次数",
      "tool_error_count": "工具失败次数"
    },
    "metrics": {
      "tool_call_count": {
        "code": "tool_call_count",
        "unit": "次",
        "description": "工具调用行计数。",
        "data_type": "integer",
        "aggregation": "count",
        "additivity": "additive",
        "filter_expression": "",
        "format": "#,##0",
        "sort_order": 10
      },
      "tool_error_count": {
        "code": "tool_error_count",
        "unit": "次",
        "description": "只统计 tool_success=false 的调用。",
        "data_type": "integer",
        "aggregation": "count",
        "additivity": "additive",
        "filter_expression": "tool_success = false",
        "format": "#,##0",
        "sort_order": 20
      }
    },
    "fields": {
      "tool_code": {
        "data_type": "text",
        "role": "key",
        "description": "工具调用事实上的工具成员键。",
        "is_enum": false,
        "sort_order": 10
      },
      "input_prompt": {
        "data_type": "text",
        "role": "display",
        "description": "所属轮次（同 message_id）的最近一条用户输入。",
        "is_enum": false,
        "sort_order": 20
      }
    },
    "enums": {}
  },
  "definitions": {
    "fact": {
      "fact_id": "fact_agent_tool_context",
      "name": "AgentToolContextFact",
      "grain": "session_id + message_id + tool_call_id",
      "description": "工具调用事实 + 所属轮次上下文；一次工具调用一行，轮次字段来自同一条 assistant 消息。"
    },
    "columns": [
      {
        "alias": "tool_code",
        "kind": "fact_field",
        "code": "tool_code",
        "name": "工具编码",
        "description": "工具调用事实上的工具成员键。",
        "data_type": "text",
        "role": "key",
        "is_enum": false,
        "source": {
          "source_schema": "diag",
          "source_table": "agent_tool_call_fact",
          "source_column": "tool_code"
        }
      },
      {
        "alias": "input_prompt",
        "kind": "fact_field",
        "code": "input_prompt",
        "name": "输入提示词",
        "description": "所属轮次（同 message_id）的最近一条用户输入。",
        "data_type": "text",
        "role": "display",
        "is_enum": false,
        "source": {
          "source_schema": "diag",
          "source_table": "agent_turn_fact",
          "source_column": "input_prompt"
        }
      },
      {
        "alias": "tool_call_count",
        "kind": "base_metric",
        "code": "tool_call_count",
        "name": "工具调用次数",
        "description": "工具调用行计数。",
        "data_type": "integer",
        "unit": "次",
        "aggregation": "count",
        "additivity": "additive",
        "filter_expression": "",
        "source": {
          "source_schema": "diag",
          "source_table": "agent_tool_call_fact",
          "source_column": "tool_call_id"
        }
      },
      {
        "alias": "tool_error_count",
        "kind": "base_metric",
        "code": "tool_error_count",
        "name": "工具失败次数",
        "description": "只统计 tool_success=false 的调用。",
        "data_type": "integer",
        "unit": "次",
        "aggregation": "count",
        "additivity": "additive",
        "filter_expression": "tool_success = false",
        "source": {
          "source_schema": "diag",
          "source_table": "agent_tool_call_fact",
          "source_column": "tool_call_id"
        }
      }
    ],
    "dimensions": [],
    "mappings": [
      {
        "semantic_kind": "non_metric_field",
        "semantic_id": "ff_ctx_tool_code",
        "source_schema": "diag",
        "source_table": "agent_tool_call_fact",
        "source_column": "tool_code"
      },
      {
        "semantic_kind": "non_metric_field",
        "semantic_id": "ff_ctx_input_prompt",
        "source_schema": "diag",
        "source_table": "agent_turn_fact",
        "source_column": "input_prompt"
      },
      {
        "semantic_kind": "base_metric",
        "semantic_id": "bm_ctx_tool_call_count",
        "source_schema": "diag",
        "source_table": "agent_tool_call_fact",
        "source_column": "tool_call_id"
      },
      {
        "semantic_kind": "base_metric",
        "semantic_id": "bm_ctx_tool_error_count",
        "source_schema": "diag",
        "source_table": "agent_tool_call_fact",
        "source_column": "tool_call_id"
      }
    ]
  }
}$json$::jsonb WHERE scenario_id = 'sc_semantic_tool_context_full';
COMMIT;
