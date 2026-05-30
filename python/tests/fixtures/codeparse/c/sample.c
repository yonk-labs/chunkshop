#include <stdlib.h>

struct Calculator {
    int base;
};

int target(int v) {
    return v * 2;
}

int caller(int v) {
    return target(v);
}
