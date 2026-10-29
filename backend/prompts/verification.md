# Verification Prompt

Write self-checking Verilog testbenches for simple digital designs.

Required checks for the counter demo:
- Active-low asynchronous reset clears count.
- Enable-high cycles increment count.
- Enable-low cycles hold state.
- Overflow asserts at max count while enabled.
- Counter wraps to zero after the max value.

