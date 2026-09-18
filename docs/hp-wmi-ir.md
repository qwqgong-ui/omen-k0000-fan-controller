# hp-wmi IR / 表面温度传感器状态

## 当前 sysfs 检查结果

在这台 8A4D 机器上，`hp-wmi` 暴露的 hwmon 属性**随内核版本变化**。Linux 7.3 起：

```text
/sys/devices/platform/hp-wmi/hwmon/hwmon9/fan1_input
/sys/devices/platform/hp-wmi/hwmon/hwmon9/fan2_input
/sys/devices/platform/hp-wmi/hwmon/hwmon9/pwm1
/sys/devices/platform/hp-wmi/hwmon/hwmon9/pwm1_enable
/sys/devices/platform/hp-wmi/hwmon/hwmon9/pwm2          # 7.3 新增
```

Linux 7.2 及更早只有前四条，**没有 `pwm2`**。详见下一节。

`hp` hwmon 下面没有 `temp*_input`。`/sys/class/thermal` 里只有 ACPI、CPU 和无线网卡温度，没有 IR、surface、skin、ambient 或 board 之类的温度节点。

7.3 另外新增了 `/sys/devices/platform/hp-wmi/gpu_mux_mode`（显卡 MUX 切换），取值 `0=HYBRID 1=DISCRETE 2=OPTIMUS 3=UMA`，写入前驱动会用硬件掩码校验该模式是否被支持。

## 内核 7.2 → 7.3 的 hp-wmi 风扇接口变更

7.3 把单一 PWM 通道拆成了每风扇独立通道。这个变更**静默打破了只写 `pwm1` 的用户态风扇控制器**——写入依然返回成功，只是从此少管了一个风扇。

通道定义（`drivers/platform/x86/hp/hp-wmi.c`）：

```c
/* 7.1-rc7 / 7.2-rc1 / 7.2-rc2 —— 三者完全一致，单通道 */
HWMON_CHANNEL_INFO(pwm, HWMON_PWM_ENABLE | HWMON_PWM_INPUT),

/* 7.3-rc1 —— 新增第二个通道（只有 input，没有 enable） */
HWMON_CHANNEL_INFO(pwm, HWMON_PWM_ENABLE | HWMON_PWM_INPUT, HWMON_PWM_INPUT),
```

驱动内部状态也随之拆开。7.2 只有一个 `priv->pwm`，手动模式下 `hp_wmi_apply_fan_settings()` 调用
`hp_wmi_fan_speed_set(priv, pwm_to_rpm(priv->pwm, priv))`，**一个值驱动整机两个风扇**。

7.3 改成两个值，按 channel 分发，并以双字节数组下发给固件：

```c
#define CPU_FAN 0   /* → pwm1 */
#define GPU_FAN 1   /* → pwm2 */

if (channel == CPU_FAN)
        priv->cpu_pwm = rpm_to_pwm(rpm, priv);
else if (channel == GPU_FAN)
        priv->gpu_pwm = rpm_to_pwm(rpm, priv);

/* hp_wmi_fan_speed_set() */
u8 fan_speed[2];
fan_speed[CPU_FAN] = pwm_to_rpm(priv->cpu_pwm, priv);
fan_speed[GPU_FAN] = pwm_to_rpm(priv->gpu_pwm, priv);
```

于是 `pwm1` 只再管 CPU 风扇，GPU 风扇有自己的值。顺带这也确定了 **`fan2` / `pwm2` 就是 GPU 风扇**，是内核自己的命名。

### 为什么 GPU 风扇恰好停在 0 RPM

7.3 切入手动模式时会用当前转速给两个值播种：

```c
cpu_rpm = hp_wmi_get_active_fan_speed(CPU_FAN);
gpu_rpm = hp_wmi_get_active_fan_speed(GPU_FAN);
priv->cpu_pwm = rpm_to_pwm(cpu_rpm / 100, priv);
priv->gpu_pwm = rpm_to_pwm(gpu_rpm / 100, priv);
priv->mode = PWM_MODE_MANUAL;
```

开机时 dGPU 是凉的、固件把 GPU 风扇关着，于是 `gpu_rpm = 0` → `gpu_pwm = 0`。只要控制器不写 `pwm2`，这个 0 就整个会话不会被改动——整机散热全压在 CPU 风扇上。

### 其他相关变更

7.3 把原先 Victus-S 专用的门禁泛化成了按板卡的能力表：

- `victus_s_thermal_profile_boards[]` → `hp_wmi_feature_boards[]`
- `is_victus_s_thermal_profile()` → `hp_wmi_fan_control_supported()` / `hp_wmi_fan_table_supported()`
- `setup_active_thermal_profile_params()` → `setup_active_board_params()`
- 新增 `hp_wmi_get_active_fan_speed()`、`hp_wmi_fan_profile()` 和 GPU MUX 一组函数

8A4D 在两个版本里都在表内（7.2 指向 `omen_v1_legacy_thermal_params`，7.3 指向 `omen_v1_legacy_board_params`），所以 7.2 上 `pwm1` 本来就可见可写；变的是语义，不是可用性。

### 对本项目的影响

`FanWriter` 现在发现 hwmon 节点下**所有** `pwmN` 通道并写入同一个档位。这既修好了 7.3，也**向后兼容**：在 7.2 及更早只存在 `pwm1`，`find_pwm_paths()` 只会发现一个通道，行为和以前完全一致。

同一档位写给两个风扇也符合固件模型——平台 JSON 里 `Fan_Table_CPU/GPU/IR_*` 都是"传感器 → 单一风扇档位"的映射，没有分风扇的速度表。

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

不过在 NVIDIA 机器上 GPU 分支实际从未生效过，启动日志一直是
`WARNING missing sensors will be ignored: GPU`。原因与 `hp-wmi` 无关：NVIDIA 的内核模块不注册 hwmon 温度节点（符号级确认，610.57.04 的 `nvidia.ko` / `nvidia-modeset.ko` / `nvidia-drm.ko` / `nvidia-uvm.ko` 对 hwmon 符号的引用数均为 0；对照 `hp_wmi.ko` 有 `devm_hwmon_device_register_with_info`），温度只走 NVML。`find_temp_sensors()` 扫 `/sys/class/hwmon` 找不到名为 `nvidia` 的节点，只有 `nouveau` 才会注册。要读 GPU 温度需要改用 NVML。
