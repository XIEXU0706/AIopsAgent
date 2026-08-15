name: redis_fault_diagnosis
description: Redis 故障排查技能：OOM、大 Key、主从切换
risk_level: medium
triggers:
  - error_type: redis_oom
  - error_type: redis_latency
  - error_type: redis_replication
steps:
  - type: check_memory_usage
  - type: analyze_big_keys
  - type: check_replication
  - type: suggest_remediation
