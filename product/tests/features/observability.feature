Feature: Trace and measure every request
  Support answers "what happened with request X?" with the request id,
  the JSON log and the process metrics, without storing what people ask.

  Scenario: Every response carries a generated request id
    Given the HR Assistant with the fake model
    When I check the health endpoint
    Then the response carries a UUID request id

  Scenario: The client request id is kept in the header and in the body
    Given the HR Assistant with the fake model
    When I ask "¿Quién es EMP-0004?" with the request id "demo-123"
    Then the header and the body carry the request id "demo-123"

  Scenario: Asking for an employee logs audit, agent_run and request without text
    Given the HR Assistant with the fake model
    When I ask "¿Quién es EMP-0004?" with the request id "demo-123"
    Then the log events for "demo-123" are "audit,agent_run,request"
    And the audit line records the tool "get_employee" for "EMP-0004"
    And the log never contains the question or the answer

  Scenario: An internal error answers 500 with the request id and only the error type
    Given the HR Assistant with a broken model
    When I ask "¿Quién es EMP-0004?" with the request id "boom-1"
    Then the response status is 500
    And the body is the generic internal error for "boom-1"
    And the error line for "boom-1" has only the error type "RuntimeError"

  Scenario: Metrics report requests, latency percentiles, tokens and the SLO
    Given the HR Assistant with the fake model
    When I ask "¿Quién es EMP-0004?" with the request id "demo-123"
    And I read the metrics
    Then the metrics count 1 request with p50 and p95 of 5.0 ms
    And the metrics report the tokens of that answer
    And the SLO is met
