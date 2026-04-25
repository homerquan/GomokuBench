import sys


class BenchmarkProgress:
    def __init__(self, total_rounds, stream=None, width=30):
        self.total_rounds = max(1, total_rounds)
        self.stream = stream or sys.stdout
        self.width = width

    def update(self, completed_rounds):
        completed_rounds = max(0, min(completed_rounds, self.total_rounds))
        ratio = completed_rounds / self.total_rounds
        percent = int(round(ratio * 100))
        filled = int(round(ratio * self.width))
        bar = "#" * filled + "-" * (self.width - filled)
        self.stream.write(
            f"\rBenchmark progress [{bar}] {percent:>3}% ({completed_rounds}/{self.total_rounds})"
        )
        self.stream.flush()

    def finish(self):
        self.update(self.total_rounds)
        self.stream.write("\n")
        self.stream.flush()

    def newline(self):
        self.stream.write("\n")
        self.stream.flush()
