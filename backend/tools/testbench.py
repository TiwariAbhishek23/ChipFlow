"""Testbench generation tools."""

from __future__ import annotations

from pydantic import BaseModel


class TestbenchInput(BaseModel):
    specification: str
    module_name: str
    width: int = 4


class TestbenchOutput(BaseModel):
    testbench: str
    summary: str


def write_testbench(payload: TestbenchInput) -> TestbenchOutput:
    width = payload.width
    max_value = (1 << width) - 1
    tb = f"""`timescale 1ns/1ps

module tb_{payload.module_name};
    reg clk;
    reg rst_n;
    reg en;
    wire [{width - 1}:0] count;
    wire overflow;

    {payload.module_name} dut (
        .clk(clk),
        .rst_n(rst_n),
        .en(en),
        .count(count),
        .overflow(overflow)
    );

    initial begin
        clk = 1'b0;
        forever #5 clk = ~clk;
    end

    task expect_count;
        input [{width - 1}:0] expected;
        input [160*8:1] message;
        begin
            #1;
            if (count !== expected) begin
                $display("ASSERT_FAIL: %0s expected count=%0d observed=%0d", message, expected, count);
                $finish;
            end
        end
    endtask

    initial begin
        rst_n = 1'b0;
        en = 1'b0;
        repeat (2) @(posedge clk);
        expect_count({width}'d0, "active-low reset should clear count");

        rst_n = 1'b1;
        en = 1'b1;
        repeat (3) @(posedge clk);
        expect_count({width}'d3, "enabled counter should advance");

        en = 1'b0;
        repeat (2) @(posedge clk);
        expect_count({width}'d3, "disabled counter should hold");

        en = 1'b1;
        repeat ({max_value - 3}) @(posedge clk);
        expect_count({width}'d{max_value}, "counter should reach max value before wrap");
        #1;
        if (overflow !== 1'b1) begin
            $display("ASSERT_FAIL: overflow flag should assert at max count when enabled");
            $finish;
        end

        @(posedge clk);
        expect_count({width}'d0, "counter should wrap to zero after max");
        if (overflow !== 1'b0) begin
            $display("ASSERT_FAIL: overflow flag should deassert after wrap");
            $finish;
        end

        $display("CHIPFLOW_PASS: counter behavior verified");
        $finish;
    end
endmodule
"""
    return TestbenchOutput(
        testbench=tb,
        summary=(
            "Wrote a self-checking testbench for reset, enable hold, counting, "
            "overflow, and wrap behavior."
        ),
    )

