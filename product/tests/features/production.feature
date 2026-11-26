Feature: Only clients with a key get in, and nobody abuses the service
  Each client has its own key, the key is never written anywhere, and each
  client has a limit of questions per minute.

  Scenario: Asking without a key is rejected and audited without the key
    Given the HR Assistant with the fake model
    When a client asks "¿Quién es EMP-0004?" without a key
    Then the response status is 401
    And the body says the key is invalid or missing
    And the audit records "auth_failed" because "missing"

  Scenario: Asking with a wrong key is rejected
    Given the HR Assistant with the fake model
    When a client asks "¿Quién es EMP-0004?" with the key "wrong-key-0123456789"
    Then the response status is 401
    And the audit records "auth_failed" because "invalid"
    And the log never contains "wrong-key-0123456789"

  Scenario: The page and the health check need no key
    Given the HR Assistant with the fake model
    When a visitor opens the page and the health check without a key
    Then both answer 200 and the page has the key field

  Scenario: Too many questions answer 429 with Retry-After
    Given the HR Assistant limited to 2 questions per minute
    When the client asks "¿Quién es EMP-0004?" 3 times
    Then the answers are 200, 200 and 429
    And the last answer says when to retry
    And the audit records "rate_limited" for the client

  Scenario: The client name is logged and audited, never its key
    Given the HR Assistant with the fake model
    When the client asks "¿Quién es EMP-0004?" with its key
    Then the response status is 200
    And every line of that request names the client
    And the log never contains the client key
