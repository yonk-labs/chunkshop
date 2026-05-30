def target(v)
  v * 2
end

module Helpers
end

class Calculator
  def add(a, b)
    target(a + b)
  end

  def multiply(a, b)
    a * b
  end
end
