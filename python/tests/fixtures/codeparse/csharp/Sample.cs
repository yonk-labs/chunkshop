using System;

namespace Calc
{
    public interface IOp
    {
        int Apply(int v);
    }

    public class Calculator
    {
        public int Base;

        public int Add(int a, int b)
        {
            int Helper(int x) => Target(x);  // local function (orphan trigger)
            return Helper(a + b);
        }

        public int Target(int v)
        {
            return v * 2;
        }
    }
}
