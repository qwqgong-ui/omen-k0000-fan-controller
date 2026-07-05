# hp-wmi IR / 表面温度传感器状态

## 当前 sysfs 检查结果

在这台 8A4D 机器上，Linux 当前只通过 `hp-wmi` 暴露了：

```text
/sys/devices/platform/hp-wmi/hwmon/hwmon9/fan1_input
/sys/devices/platform/hp-wmi/hwmon/hwmon9/fan2_input
/sys/devices/platform/hp-wmi/hwmon/hwmon9/pwm1
/sys/devices/platform/hp-wmi/hwmon/hwmon9/pwm1_enable
```

`hp` hwmon 下面没有 `temp*_input`。`/sys/class/thermal` 里只有 ACPI、CPU 和无线网卡温度，没有 IR、surface、skin、ambient 或 board 之类的温度节点。

## Windows 端命令路径

OMEN 反编译代码读取平台传感器时使用：

```csharp
byte[] input = new byte[4] { index, 0, 0, 0 };
byte[] data = _omenHsaClient.BiosWmiCmd_GetSync(131080, 35, input, input.Length, 4);
return data[0];
```

`PerformanceControlHelper.cs` 里的索引含义：

- `0`: IR 传感器，部分平台可能切到 board sensor
- `1`: ambient / board 传感器
- `2`: PCH 传感器
- `3`: VR 传感器

`131080` 是 `0x20008`，也就是 `linux/hp-wmi.c` 里已有的 `HPWMI_GM` WMI 通道。命令类型 `35` 是 `0x23`。当前 `linux/hp-wmi.c` 已经有风扇、GPU thermal mode、风扇表和 power-limit 等 GM 命令，但没有实现或导出这个传感器读取命令。

## 可能需要补的内核接口

如果要让用户态稳定读取 IR，`hp-wmi` 大概率需要新增 GM command type `0x23` 的查询包装，并注册 hwmon 温度通道，例如：

- `temp1_input`: IR / surface，索引 `0`
- 可选 `temp2_input`: ambient / board，索引 `1`
- 可选 `temp3_input`: PCH，索引 `2`
- 可选 `temp4_input`: VR，索引 `3`

真正 upstream 前需要按机型校验命令可用性，只暴露返回值合理的通道。在这个接口补齐前，omen-k0000-fan-controller默认使用 CPU 和 GPU；其中 GPU 只在 dGPU 已经 `runtime_status=active` 时读取温度，避免唤醒 D3/suspended 状态。
