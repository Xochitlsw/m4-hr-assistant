Feature: Ask the HR Assistant
  The HR staff asks about employees and the assistant answers with the
  read-only directory tools, in a loop with a step cap.

  Scenario: Asking for an employee by id uses get_employee
    Given the HR Assistant with the fake model
    When I ask "¿Quién es EMP-0004?"
    Then the answer mentions "Jorge Díaz"
    And the tools used are "get_employee"
    And the steps count is 2

  Scenario: Asking for a department lists its people
    Given the HR Assistant with the fake model
    When I ask "¿Quiénes trabajan en Finanzas?"
    Then the answer mentions "Sofía Gómez"
    And the tools used are "search_by_department"

  Scenario: An unknown employee is reported
    Given the HR Assistant with the fake model
    When I ask "¿Quién es EMP-9999?"
    Then the response status is 200
    And the answer mentions "No existe un empleado con id EMP-9999"

  Scenario: A question without an employee or a department gets guidance
    Given the HR Assistant with the fake model
    When I ask "¿Qué día es hoy?"
    Then the answer is "Pregúntame por un empleado (EMP-0001) o un departamento."
    And no tools were used

  Scenario: The step cap stops the loop
    Given the HR Assistant limited to 1 step
    When I ask "¿Quién es EMP-0004?"
    Then the answer is "No pude completar la respuesta. Intenta con una pregunta más concreta."
    And the steps count is 1

  Scenario Outline: An empty or too long question is rejected
    Given the HR Assistant with the fake model
    When I ask a question of <length> characters
    Then the response status is 422

    Examples:
      | length |
      | 0      |
      | 2001   |
