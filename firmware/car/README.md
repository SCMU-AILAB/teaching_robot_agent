# 小车底盘固件（Rust）

目标板：MINDA-01 四路电机驱动板，STM32F407VET6。框架：embassy-stm32 0.6 + embassy-executor 0.10。

当前只是开发链路验证程序：PE10 LED 以 1 Hz 闪烁，PA8 蜂鸣器保持静音；USART1、USART2、USART3 每秒各打印一行 `hello from USARTx t=Ns`（115200 8N1）。电机控制、编码器、通信协议和看门狗尚未实现，需另行设计审核。

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
