public class Lambdas {
    // target is a method.
    int target(int v) {
        return v * 2;
    }

    // runner makes a call from inside a lambda. Java has no nested method
    // declarations, so the lambda body is not its own emitted symbol — the
    // call to target() must attribute to runner, not orphan.
    int runner(int v) {
        java.util.function.IntUnaryOperator op = x -> target(x);
        return op.applyAsInt(v);
    }
}
