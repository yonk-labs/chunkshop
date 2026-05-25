package com.example.sample;

public class Calculator {

    public int add(int a, int b) {
        int total = a + b;
        return helper(total);
    }

    public int multiply(int a, int b) {
        return a * b;
    }

    public static int helper(int value) {
        return value * 2;
    }
}
