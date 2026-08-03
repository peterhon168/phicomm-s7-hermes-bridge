# S7 原固件备份与 zS7 刷写门禁

## 当前边界

本阶段只刷可上报体重的 zS7。zS7 作者明确标注“当前不支持体脂”；
手持电极缺失时，也不具备完整 8 电极 BIA 的物理测量条件。

## 硬件与风险

需要：

- 支持 **3.3 V 逻辑**的 USB-TTL；
- 可靠连线，必要时使用独立稳定 3.3 V 供电；
- 绝缘工作面、拍照记录和两个不同的备份介质。

必须先断开秤内电池。不得把 5 V 接到 ESP8266 的 3.3 V 电源或 IO；
不要同时让秤电池和 USB-TTL 向同一电源网供电。引脚和测试点以
zS7 刷机 Wiki 的实物图为准，不仅凭文字猜测。

## 1. 建立工具环境

esptool v5 的 ESP8266 命令支持读取 MAC、Flash ID 与整片 Flash：

```bash
python3 -m venv .firmware-tools
.firmware-tools/bin/pip install esptool
.firmware-tools/bin/esptool version
```

`.firmware-tools` 和后续固件备份不应提交到代码仓库。

## 2. 进入下载模式并只读识别

按 zS7 Wiki 实物图连接 `GND/TX/RX/3.3V`，上电时保持 ESP8266
`GPIO0` 接地以进入 ROM 下载模式。把下面的 `PORT` 换成实际串口：

```bash
.firmware-tools/bin/esptool --chip ESP8266 -p PORT flash-id
.firmware-tools/bin/esptool --chip ESP8266 -p PORT read-mac
```

如果无法同步，先断电排查交叉的 TX/RX、共地、GPIO0 时序和供电稳定性；
不要用反复带电插拔来“试”。

## 3. 连续读取两份完整备份

```bash
mkdir -p firmware-backups
.firmware-tools/bin/esptool --chip ESP8266 -p PORT -b 460800 \
  read-flash 0 ALL firmware-backups/original-a.bin
```

完成后断电，重新按照下载模式上电，再读第二份：

```bash
.firmware-tools/bin/esptool --chip ESP8266 -p PORT -b 460800 \
  read-flash 0 ALL firmware-backups/original-b.bin
scripts/verify_firmware_backups.sh \
  firmware-backups/original-a.bin firmware-backups/original-b.bin EXPECTED_FLASH_BYTES
```

把 `EXPECTED_FLASH_BYTES` 换成 `flash-id` 实测容量：512 KiB=`524288`、
1 MiB=`1048576`、2 MiB=`2097152`、4 MiB=`4194304`、8 MiB=`8388608`、
16 MiB=`16777216`。
如果 `460800` 不稳定，降为 `115200` 重读两份。验证脚本只有在文件非空且
两个路径/文件 inode 不同、各自长度等于实测 Flash 容量、且逐字节一致时
才返回成功，并输出 SHA-256。

## 4. 备份门禁

满足以下全部条件才能刷写：

- `original-a.bin` 与 `original-b.bin` 是两次独立读取，各自长度与 Flash ID
  容量一致，且 SHA-256 完全相同；
- 已保存 `flash-id` 和 `read-mac` 的终端输出；
- 相同的原厂固件备份已再复制到另一个物理介质；
- 已从 zS7 项目本身核对目标固件的来源、S7/S7PE 型号和长度；
- 已记录目标固件 SHA-256。

因 zS7 发布物与板本需结合实物确认，本项目不内置来源不明的 `.bin`，
也不提供一键写入脚本。进入这个门禁时，先把上述输出和文件哈希交给我复核，
再根据实际固件包确定写入地址。

## 5. 刷后验收与回退

刷后先不装回外壳，完成：正常启动、秤面显示、Wi-Fi 配网、MQTT 连接、
各做一次两个测试用户的稳定称重。如果刷写失败，使用已验证的原厂完整备份进行回退；
回退地址同样必须根据实际 Flash 布局复核，不在未确认前猜测。

## 权威参考

- [zS7 固件与限制](https://github.com/a2633063/zS7)
- [zS7 固件烧录 Wiki](https://github.com/a2633063/zS7/wiki/%E5%9B%BA%E4%BB%B6%E7%83%A7%E5%BD%95)
- [esptool ESP8266 命令](https://docs.espressif.com/projects/esptool/en/latest/esp8266/esptool/basic-commands.html)
