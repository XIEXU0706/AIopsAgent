name: mysql_fault_diagnosis
description: MySQL 故障排查技能：连接数暴涨、慢查询、主从延迟
risk_level: high
triggers:
  - error_type: mysql_connection
  - error_type: mysql_slow_query
  - error_type: mysql_replication
steps:
  - type: check_connections
  - type: analyze_slow_queries
  - type: check_replication_status
  - type: suggest_remediation
