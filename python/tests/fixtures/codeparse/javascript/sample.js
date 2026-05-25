export function helper(value) {
    return value * 2;
}

export class Calculator {
    add(a, b) {
        const total = a + b;
        return helper(total);
    }

    multiply(a, b) {
        return a * b;
    }
}
