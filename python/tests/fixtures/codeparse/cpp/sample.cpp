#include <vector>

namespace calc {

int target(int v) {
    return v * 2;
}

class Calculator {
public:
    int base;
    int add(int a, int b) {
        auto step = [](int x) { return target(x); };
        return step(a + b);
    }
};

}  // namespace calc
