import cocotb
from cocotb_coverage.coverage import *
from cocotb_coverage.crv import *
from cocotb.queue import Queue
from cocotb.triggers import Event, RisingEdge, ReadOnly, FallingEdge
from cocotb.clock import Clock
from cocotb_bus.drivers import BusDriver
from cocotb_bus.monitors import BusMonitor
from cocotb.result import TestSuccess


# this is a simple alu model made in python
def alu_model(a, b, op):
    if op == 0:
        return a + b, "ADD"
    elif op == 1:
        return a ^ b, "XOR"
    elif op == 2:
        return a & b, "AND"
    elif op == 3:
        return a | b, "OR"


# A Transaction class in cocotb can be extended from the randomized class and
# we can define the random variables using add_rand
class ALUTransaction(Randomized):
    count = 0

    def __init__(self):
        Randomized.__init__(self)
        self.a = 0
        self.b = 0
        self.op = 0
        self.c = 0
        self.out = 0
        self.add_rand("a", list(range(0, 16)))
        self.add_rand("b", list(range(0, 16)))
        self.add_rand("op", list(range(0, 4)))

    def Print_t(self, name):
        cocotb.log.info(
            "[@transaction " + str(ALUTransaction.count) + "][" + name + "] a: " + str(hex(self.a)) + " b: " + str(
                hex(self.b)) + " op: " + str(hex(self.op)) + " out: " + str(hex(self.out)) + " c: " + str(
                hex(self.c)))

    def increase_trans(self):
        ALUTransaction.count = ALUTransaction.count + 1


# the first method to create a covergroup just as in system verilog is to create
# a coverage section and inisde of it we can add the coverpoints and the cross
# coverage then using a decorator with the name of the coverage section
# we can make a function each time this function is called it gets sampled
# unlike system verilog where we can use .sample() function.
@CoverPoint("top.a", vname="a", bins=list(range(0, 16)))
@CoverPoint("top.b", vname="b", bins=list(range(0, 16)))
@CoverPoint("top.op", vname="op", bins=list(range(0, 4)))
@CoverCross("top.all_cases", items=["top.a", "top.b", "top.op"])
def log(a, b, op):
    cocotb.log.info("the randomized values are a=" + str(a) + "  b=" + str(b) + "  op=" + str(op))


# in this implementation, in the generator we will use queues and events
# the queue is the replacement of the mailbox, we will use cocotb Queue
# from the library cocotb.queue, so each time the generator will randomize
# the data and then put in the queue and and await the event to be set
# by the driver indicating its finished.
class ALU_generator:
    def __init__(self, gen_queue, gen_event, lock):
        self.pkt = ALUTransaction()
        self.gen_queue = gen_queue
        self.gen_event = gen_event
        self.lock = lock

    async def gen(self):
        self.lock.clear()
        for i in range(8000):
            self.pkt.randomize()
            await self.gen_queue.put(self.pkt)
            log(self.pkt.a, self.pkt.b, self.pkt.op)
            self.pkt.increase_trans()
            await self.gen_event.wait()
        self.lock.set()


# in this implementation the driver will synchronize with the monitor with
# a clock added in the testbench, i used the bus extensions Busdriver and Busmonitor
class ALU_driver(BusDriver):
    _signals = ['a', 'b', 'op', 'c', 'out']

    def __init__(self, dut, name, clock, driv_queue, driv_gen_event):
        BusDriver.__init__(self, dut, name, clock)
        self.clock = clock
        self.bus = dut
        self.driv_queue = driv_queue
        self.driv_gen_event = driv_gen_event

    async def _driver_send(self, transaction, sync):
        transaction.Print_t(self.name)
        self.bus.a.value = transaction.a
        self.bus.b.value = transaction.b
        self.bus.op.value = transaction.op
        await FallingEdge(self.clock)

    async def drv(self):
        while True:
            self.driv_gen_event.clear()
            pkt = await self.driv_queue.get()
            await self.send(pkt, sync=False)
            await RisingEdge(self.clock)
            self.driv_gen_event.set()


class ALU_monitor(BusMonitor):
    _signals = ["a", "b", "op", "c", "out"]

    def __init__(self, dut, name, clock, mon_queue):
        BusMonitor.__init__(self, dut, name, clock)
        self.clock = clock
        self.bus = dut
        self.mon_queue = mon_queue
        self.pkt = ALUTransaction()

    async def _monitor_recv(self):
        for i in range(8000):
            await RisingEdge(self.clock)
            await ReadOnly()
            self.pkt.a = self.bus.a.value
            self.pkt.b = self.bus.b.value
            self.pkt.op = self.bus.op.value
            self.pkt.out = self.bus.out.value
            self.pkt.c = self.bus.c.value
            self.pkt.Print_t(self.name)
            await self.mon_queue.put(self.pkt)


# in this implementation the scoreboard starts by receiving data
# from the monitor then go to the alu model made and get the
# expected output and compare it with the real output
class ALU_scoreboard:
    def __init__(self, sco_queue):
        self.Unique_bugs = []
        self.sco_queue = sco_queue
        self.nofpass = 0
        self.noffail = 0
        self.name = "SCO"

    async def Start_checking(self):
        while True:
            pkt = await self.sco_queue.get()
            pkt.Print_t(self.name)
            [expected_out, OP_Name] = alu_model(pkt.a, pkt.b, pkt.op)
            operation = int(str(pkt.a) + str(pkt.b) + str(pkt.op))
            if pkt.c == 1:
                pkt.c = 16
            else:
                pkt.c = 0
            Real_out = pkt.c + pkt.out
            if Real_out == expected_out:
                cocotb.log.info(OP_Name + " Test Case passed")
                self.nofpass = self.nofpass + 1
            else:
                cocotb.log.info(OP_Name + " Test Case Failed")
                if operation not in self.Unique_bugs:
                    self.Unique_bugs.append(operation)
                    print("done")
                self.noffail = self.noffail + 1


# the enviroment class between monitor driver and scoreboard
class ALU_enviroment:
    def __init__(self, dut, driv_queue, driv_gen_event):
        self.sco_mon_queue = Queue()
        self.dut = dut
        self.driv = ALU_driver(self.dut, "DRIV", self.dut.CLK, driv_queue, driv_gen_event)
        self.monitor = ALU_monitor(self.dut, "MON", self.dut.CLK, self.sco_mon_queue)
        self.sco = ALU_scoreboard(self.sco_mon_queue)

    async def run(self):
        await cocotb.start(self.driv.drv())
        await cocotb.start(self.sco.Start_checking())


# the test class between the enviroment and the generator
class ALU_test:
    def __init__(self, dut, lock_event):
        self.driv_queue = Queue()
        self.driv_gen_event = Event(name=None)
        self.dut = dut
        self.env = ALU_enviroment(self.dut, self.driv_queue, self.driv_gen_event)
        self.gen = ALU_generator(self.driv_queue, self.driv_gen_event, lock_event)

    async def run(self):
        self.env.driv.driv_queue = self.gen.gen_queue
        self.env.driv.driv_gen_event = self.gen.gen_event
        await cocotb.start(self.gen.gen())
        await cocotb.start(self.env.run())


@cocotb.test()
async def test_alu(dut):
    my_lock = Event(name=None)  # this lock is used to prevent the test module from
    # ending as there is an event controlled by the generator is cleared and not
    # set until all the loop ends
    CLK = Clock(dut.CLK, 10, units="ns")
    test = ALU_test(dut, my_lock)
    await cocotb.start(CLK.start())
    await cocotb.start(test.run())
    await my_lock.wait()
    cocotb.log.info("number of passes are " + str(test.env.sco.nofpass) + " while the number of fails are " + str(
        test.env.sco.noffail) + " while number of unique bugs are " + str(len(test.env.sco.Unique_bugs)))
    coverage_db.export_to_xml(filename="ALU_coverage.xml")
    raise TestSuccess
