package main

// target is a free function.
func target(v int) int {
	return v * 2
}

// runner makes a call from inside a func literal (closure). Go has no nested
// function *declarations*, so the closure is not an emitted symbol — the
// call to target() must attribute to runner (the enclosing func), not orphan.
func runner(v int) int {
	apply := func(x int) int {
		return target(x)
	}
	return apply(v)
}
