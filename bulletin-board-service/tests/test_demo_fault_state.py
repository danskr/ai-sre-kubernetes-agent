from app.demo_faults import DbLeakFaultController


def test_fault_starts_and_stops():
    fault = DbLeakFaultController()
    assert fault.status()['enabled'] is False
    assert fault.start()['enabled'] is True
    assert fault.stop()['enabled'] is False
