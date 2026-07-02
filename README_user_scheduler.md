# 暗影精灵8A4D用户调度器

这是一个可安装的 Linux 用户态风扇调度程序，用暗影精灵 / OMEN 8A4D 机型对应的 Hendricks N20E 风扇表，通过 `hp-wmi` 的 hwmon PWM 接口控制风扇。

默认策略：

- 只监控 CPU 温度：`--sensors CPU`
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
omen-8a4d-user-scheduler --help
```

systemd 服务为：

```bash
sudo systemctl enable --now omen-8a4d-user-scheduler.service
```

## 源码树 dry-run

```bash
python3 omen_8a4d_user_scheduler.py --dry-run --once --simulate CPU=82
```

查看真实传感器和 `hp-wmi` 发现结果：

```bash
sudo omen-8a4d-user-scheduler --dry-run --once -v
```

如果 CPU 传感器自动识别不准，可以手动指定：

```bash
sudo omen-8a4d-user-scheduler --dry-run --once \
  --cpu-temp /sys/class/hwmon/hwmon7/temp1_input
```

## 实际运行

```bash
sudo omen-8a4d-user-scheduler --profile performance --sensors CPU
```

可选：同时切到内核平台性能配置：

```bash
sudo omen-8a4d-user-scheduler --profile performance --sensors CPU --platform-profile performance
```

## IR 状态

当前机器检查结果：

- `hp` hwmon 只暴露 `fan1_input/fan2_input/pwm1/pwm1_enable`
- `/sys/class/thermal` 只有 ACPI/CPU/无线温度
- 没有 `IR`、`surface`、`skin` 或类似温度节点

Windows 反编译代码里 IR 通过 `BiosWmiCmd_GetSync(131080, 35, ...)` 获取，等价于 Linux `hp-wmi` 的 GM 通道命令类型 `0x23`。现有 `linux/hp-wmi.c` 没有把这个传感器命令导出成 hwmon 温度接口，所以 IR 目前不能直接被这个用户态调度器读取。

更多细节见 [docs/hp-wmi-ir.md](docs/hp-wmi-ir.md)。

## 策略说明

每轮调度流程：

1. 读取启用的传感器，默认只有 CPU。
2. 使用 JSON 里的 `Lamda_Increase` / `Lamda_Decrease` 做非对称 EWMA 平滑。
3. 查所选 profile 的风扇表，取启用传感器对应目标档位中的最高值。
4. 将 OEM 档位值按表内最大档位映射为 `0..255` PWM。
5. 写入 `pwm1_enable=1` 和 `pwm1`。

默认周期是 1 秒，对应 OEM 配置里的 `IntervalAlgoShort=1000`。

## 常用参数

- `--profile default|performance|fan-curve`: 选择内置 Hendricks N20E 风扇表。
- `--sensors CPU`: 选择监控传感器；可用 `CPU,GPU,IR` 或 `ALL`，默认只监控 CPU。
- `--interval 1.0`: 调度周期秒数。
- `--fan-speed-max 58`: 指定 OEM 最大档位到 PWM 255 的映射，默认用所选表最大值。
- `--no-restore-auto`: 退出时不恢复 `pwm1_enable=2`。
- `--ignore-board`: 非 8A4D 主板也强制运行。
- `--dump-curve`: 打印加载后的风扇表。
