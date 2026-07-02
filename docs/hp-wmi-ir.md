# hp-wmi IR / surface sensor status

## Runtime sysfs check

On the tested 8A4D machine, Linux currently exposes:

```text
/sys/devices/platform/hp-wmi/hwmon/hwmon9/fan1_input
/sys/devices/platform/hp-wmi/hwmon/hwmon9/fan2_input
/sys/devices/platform/hp-wmi/hwmon/hwmon9/pwm1
/sys/devices/platform/hp-wmi/hwmon/hwmon9/pwm1_enable
```

There is no `temp*_input` under the `hp` hwmon device. The available thermal
zones are ACPI/CPU/wireless only; none is labeled IR, surface, skin, ambient, or
board.

## Windows command path

The OMEN decompiled code reads platform sensors through:

```csharp
byte[] input = new byte[4] { index, 0, 0, 0 };
byte[] data = _omenHsaClient.BiosWmiCmd_GetSync(131080, 35, input, input.Length, 4);
return data[0];
```

Index mapping in `PerformanceControlHelper.cs`:

- `0`: IR sensor, unless the platform switches IR to board sensor
- `1`: ambient / board sensor
- `2`: PCH sensor
- `3`: VR sensor

`131080` is `0x20008`, the `HPWMI_GM` WMI channel used by `linux/hp-wmi.c`.
Command type `35` is `0x23`. The current `linux/hp-wmi.c` enum includes fan,
GPU thermal mode, fan table, and power-limit commands, but does not include or
export this sensor read command.

## Kernel interface likely needed

To make IR available cleanly to user space, `hp-wmi` likely needs a new GM query
wrapper for command type `0x23` and hwmon temperature channels, for example:

- `temp1_input`: IR / surface sensor index `0`
- optionally `temp2_input`: ambient / board sensor index `1`
- optionally `temp3_input`: PCH sensor index `2`
- optionally `temp4_input`: VR sensor index `3`

The exact upstreamable implementation should validate the command on supported
boards and only expose channels that return sane values. Until that exists, this
program defaults to CPU-only scheduling and leaves GPU/IR out of the control
loop.
