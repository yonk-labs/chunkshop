export function helper(value: number): number {
    return value * 2;
}

export class Calculator {
    add(a: number, b: number): number {
        const total = a + b;
        return helper(total);
    }

    multiply(a: number, b: number): number {
        return a * b;
    }
}
