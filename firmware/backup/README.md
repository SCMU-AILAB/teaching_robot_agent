# MINDA-01 小车原固件备份（仅本地，不提交仓库）

- 读取日期：2026-10-21，方式：UART1（CH9102）+ STM32 系统引导程序，stm32flash 0.7
- 芯片：STM32F407VET6，Device ID 0x0413，引导程序版本 3.1
- 读保护：RDP=0xAA（Level 0，未保护）；写保护未开启
- `MINDA-01_stm32f407_flash_512k.bin`：0x08000000 起 512 KiB，两次读取 SHA-256 一致：
  d7a549e87ed37e66d9390d095f2b28c7df9e9103ec2fe7ec04dd9c5a23f254e6
  实际使用约 36.4 KiB，其余为 0xFF；初始 SP=0x20000E50，复位向量=0x0800019D
- `MINDA-01_option_bytes.bin`：0x1FFFC000 起 16 字节

## 一键下载时序（本板实测）

DTR 控制 BOOT0，RTS 控制复位。单独执行进入序列偶尔超时，先执行一次“预备”序列更稳定：

```bash
P=/dev/cu.wchusbserial5C670410431   # 以实际设备名为准
stm32flash -b 115200 -i '-rts&-dtr,dtr:rts&-dtr,dtr' $P            # 预备，会报超时，属正常
stm32flash -b 115200 -i 'dtr&rts,-rts:-dtr&rts,-rts' $P            # 进入引导程序并读取芯片信息
```

## 恢复原固件

```bash
stm32flash -b 115200 -i '-rts&-dtr,dtr:rts&-dtr,dtr' $P
stm32flash -b 115200 -i 'dtr&rts,-rts:-dtr&rts,-rts' -w MINDA-01_stm32f407_flash_512k.bin -v -S 0x08000000 $P
```
