# 小车底盘固件（Rust）

目标板：MINDA-01 四路电机驱动板，STM32F407VET6。框架：embassy-stm32 0.6 + embassy-executor 0.10。

阶段 1 固件：串口文本协议驱动麦克纳姆轮运动（前后、左右平移、旋转），编码器 PI 轮速闭环，300 ms 看门狗停车，堵转或编码器故障锁定，20 Hz 上报编码器与占空比。设计与协议见 `docs/superpowers/specs/2026-10-21-car-base-phase1-design.md`，纯逻辑在 `firmware/car-core`。

- 驱动方式：慢衰减（一路全开、另一路反相 PWM）。速度满量程 1000 = 每轮 4000 编码器计数/秒，闭环占空比上限 80%，目标转速每 10 ms 最多变化 2%。
- 通信口：USART1、USART2、USART3 均可，第一条有效命令所在口被锁定。PE10 LED 以 1 Hz 闪烁表示运行，PA8 蜂鸣器静音。

## 遥控与标定

在自己的终端运行（需要交互式键盘，不能经 Claude Code 的 `!` 执行）：

```bash
uv run --extra car python -m app.car_teleop /dev/cu.wchusbserial5C670410421          # 键盘遥控
uv run --extra car python -m app.car_teleop /dev/cu.wchusbserial5C670410421 --motor 1 --duty 1000 --seconds 2
```

## 实测记录（2026-10-21，电池 12.5 V，轮子架空）

| 项目 | 结果 |
| --- | --- |
| 轮位映射 | 左前 M4、右前 M1、左后 M3、右后 M2，正转均向前 |
| 快衰减起转门槛 | 约 50%（已弃用） |
| 慢衰减起转 | 15% 即可转动，转速近似线性 |
| 前进、左平移、逆时针旋转 | 四轮转向与运动学一致 |
| 开环落地横移 | 前后偏移（左平移时两组轮速差约 20%） |
| 闭环（架空，V 600，2 s） | 三种动作四轮计数差异 2.4%–2.7% |
| 落地测试、拔线看门狗停车 | 待测 |

## 环境

```bash
rustup target add thumbv7em-none-eabihf
rustup component add llvm-tools
cargo install cargo-binutils
brew install stm32flash
```

## 构建与检查

```bash
cd firmware/car
cargo fmt --check
cargo clippy --release -- -D warnings
cargo objcopy --release -- -O binary car-fw.bin
```

## 烧录（经板上 UART1 口）

UART1 带一键下载电路：DTR 控制 BOOT0，RTS 控制复位。单独执行进入序列偶尔超时，先执行一次预备序列（会报超时，属正常）：

```bash
P=/dev/cu.wchusbserial5C670410431   # 以实际设备名为准
stm32flash -b 115200 -i '-rts&-dtr,dtr:rts&-dtr,dtr' $P
stm32flash -b 115200 -i 'dtr&rts,-rts:-dtr&rts,-rts' -w car-fw.bin -v -S 0x08000000 $P
```

烧录完成后自动复位运行。打开 UART1 时须先将 DTR、RTS 置低，否则单片机会被复位或进入引导程序。

原固件备份与恢复方法见 `firmware/backup/README.md`（仅本地保存，不进仓库）。

## 引脚（由原固件反汇编得到，待原理图核对）

| 用途 | 引脚 |
| --- | --- |
| USART1（板上 UART1 口） | PA9 TX / PA10 RX |
| USART2 | PD5 TX / PD6 RX |
| USART3 | PD8 TX / PD9 RX |
| I2C2（QMI8658 IMU） | PB10 SCL / PB11 SDA |
| TIM3（编码器之一） | PB4 / PB5 |
| LED | PE10 |
| 有源蜂鸣器（高电平响） | PA8 |
