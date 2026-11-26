Feature: Pay less for each answer without making it worse
  Every answer says which model it used, why it was chosen and how much it
  cost, so the cost levers can be measured.

  Scenario: A simple question goes to the small model and has a cost
    Given the HR Assistant with the fake model
    When I ask "¿Quién es EMP-0004?" as request "cost-1"
    Then the answer comes from the small model because "simple"
    And the answer has a cost above zero

  Scenario: A question with a keyword goes to the large model and says why
    Given the HR Assistant with the fake model
    When I ask "Explícame quién es EMP-0004" as request "cost-2"
    Then the answer comes from the large model because "keyword:explica"

  Scenario: With routing off every question goes to the large model
    Given the HR Assistant with routing off
    When I ask "¿Quién es EMP-0004?" as request "cost-3"
    Then the answer comes from the large model because "routing_off"

  Scenario: A cached gateway answer costs nothing
    Given the HR Assistant on a gateway that answers from its cache
    When I ask "¿Quién es EMP-0004?" as request "cost-4"
    Then the answer mentions "Jorge Díaz"
    And the answer has 2 cached calls and costs nothing

  Scenario: A failing gateway answers 502 with the request id
    Given the HR Assistant on a gateway that is down
    When I ask "¿Quién es EMP-0004?" as request "down-1"
    Then the response status is 502
    And the body says the model is unavailable for "down-1"
    And the error line for "down-1" names "ModelUnavailable"
