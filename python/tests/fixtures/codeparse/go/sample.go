package sample

type Calculator struct {
	name string
}

func (c *Calculator) Add(a int, b int) int {
	total := a + b
	return helper(total)
}

func (c *Calculator) Multiply(a int, b int) int {
	return a * b
}

func helper(value int) int {
	return value * 2
}
