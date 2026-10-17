import csv


def safe_spreadsheet_value(value):
    if isinstance(value, str) and value.lstrip(" ").startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


class SafeCSVWriter:
    def __init__(self, stream, **kwargs):
        self.writer = csv.writer(stream, **kwargs)

    def writerow(self, values):
        return self.writer.writerow(safe_spreadsheet_value(value) for value in values)

    def writerows(self, rows):
        for row in rows:
            self.writerow(row)
