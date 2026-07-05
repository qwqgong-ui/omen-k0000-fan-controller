# omen-k0000-fan-controller

这是一个可安装的 Linux 用户态风扇调度程序，用 OMEN K0000 / 8A4D 机型对应的 Hendricks N20E 风扇表，通过 `hp-wmi` 的 hwmon PWM 接口控制风扇。

默认策略：

- 默认监控 CPU、GPU 和内存 SPD：`--sensors CPU,GPU,SPD`
- GPU 默认只在 dGPU 已经 `runtime_status=active` 时读取温度，D3/suspended 时跳过
- SPD 默认发现 `spd5118` 温度，多个内存温度点取最高值，并且默认 10 秒读取一次
- 默认加载 `SwFanControlCustomPerformance`
- 通过 `/sys/class/hwmon/hwmon*/pwm1` 和 `pwm1_enable` 写入风扇档位
- 退出时恢复 `pwm1_enable=2` 自动模式

Linux `hp-wmi` 当前 PWM 模式值：

- `pwm1_enable=0`: 最大风扇
- `pwm1_enable=1`: 手动 PWM
- `pwm1_enable=2`: 自动模式

## Arch / CachyOS 安装

仓库根目录已经提供 `PKGBUILD`：

```bash
makepkg -si
```

安装后命令为：

```bash
omen-k0000-fan-controller --help
```

systemd 服务为：

```bash
sudo systemctl enable --now omen-k0000-fan-controller.service
```

## 源码树 dry-run

```bash
python3 omen_k0000_fan_controller.py --dry-run --once --simulate CPU=82,SPD=70
```

查看真实传感器和 `hp-wmi` 发现结果：

```bash
sudo omen-k0000-fan-controller --dry-run --once -v
```

如果 CPU 传感器自动识别不准，可以手动指定：

```bash
sudo omen-k0000-fan-controller --dry-run --once \
  --cpu-temp /sys/class/hwmon/hwmon7/temp1_input
```

## 实际运行

```bash
sudo omen-k0000-fan-controller --profile performance --sensors CPU,GPU,SPD --gpu-temp-policy active-only
```

可选：同时切到内核平台性能配置：

```bash
sudo omen-k0000-fan-controller --profile performance --sensors CPU,GPU,SPD --gpu-temp-policy active-only --platform-profile performance
```

默认服务已经让 CPU、GPU 和 SPD 温度参与调度，同时不唤醒 D3 状态的 dGPU：

```bash
sudo omen-k0000-fan-controller --sensors CPU,GPU,SPD --gpu-temp-policy active-only
```

`active-only` 会先读 PCI `power/runtime_status`。如果状态是 `suspended`，程序跳过 GPU 温度读取；只有状态已经是 `active` 时才读取 GPU hwmon 温度。不要用 `nvidia-smi` 轮询温度，它通常会唤醒或保持 dGPU 活跃。

## SPD 默认曲线

SPD 指内存 SPD 温度，不是风扇转速。程序默认读取所有 `spd5118` 温度，取最高值参与决策。建议曲线已经加入默认配置：

```text
0C  -> 21
55C -> 21
60C -> 24
65C -> 28
70C -> 33
75C -> 40
80C -> 50
85C -> 58
```

低于 55C 时只保持性能配置的基础档位，不会因为内存空闲温度抬高风扇。默认 `--spd-interval 10`，也就是 SPD 温度每 10 秒读取一次；要更低频可以调大这个值。

## IR 状态

当前机器检查结果：

- `hp` hwmon 只暴露 `fan1_input/fan2_input/pwm1/pwm1_enable`
- `/sys/class/thermal` 只有 ACPI/CPU/无线温度
- 没有 `IR`、`surface`、`skin` 或类似温度节点

Windows 反编译代码里 IR 通过 `BiosWmiCmd_GetSync(131080, 35, ...)` 获取，等价于 Linux `hp-wmi` 的 GM 通道命令类型 `0x23`。现有 `linux/hp-wmi.c` 没有把这个传感器命令导出成 hwmon 温度接口，所以 IR 目前不能直接被这个用户态调度器读取。

更多细节见 [docs/hp-wmi-ir.md](docs/hp-wmi-ir.md)。

## 策略说明

每轮调度流程：

1. 读取启用的传感器，默认 CPU、GPU、SPD；SPD 使用低频缓存。
2. 使用 JSON 里的 `Lamda_Increase` / `Lamda_Decrease` 做非对称 EWMA 平滑。
3. 查所选 profile 的风扇表，取启用传感器对应目标档位中的最高值。
4. 将目标档位按表内最大档位映射为 `0..255` PWM。
5. 写入 `pwm1_enable=1` 和 `pwm1`。

默认周期是 1 秒，对应 OEM 配置里的 `IntervalAlgoShort=1000`。

## 常用参数

- `--profile default|performance|fan-curve`: 选择内置 Hendricks N20E 风扇表。
- `--sensors CPU,GPU,SPD`: 选择监控传感器；可用 `CPU,GPU,SPD,IR` 或 `ALL`，默认监控 CPU、GPU 和 SPD。
- `--gpu-temp-policy active-only|always`: GPU 温度读取策略，默认 `active-only`，避免唤醒 D3。
- `--spd-temp PATH`: 手动指定 SPD `temp*_input`，可以重复传入或用逗号分隔。
- `--spd-interval 10`: SPD 温度最小读取间隔秒数，默认 10 秒。
- `--interval 1.0`: 调度周期秒数。
- `--fan-level-max 58`: 指定最大档位到 PWM 255 的映射，默认用所选表最大值。
- `--no-restore-auto`: 退出时不恢复 `pwm1_enable=2`。
- `--ignore-board`: 非 8A4D 主板也强制运行。
- `--dump-curve`: 打印加载后的风扇表。
