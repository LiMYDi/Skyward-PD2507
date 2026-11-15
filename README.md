# Skyward Recovery for iQOO Neo8（PD2301 / V2301A）

vivo iQOO Neo8 的第三方 Recovery，基于 [Kirk Lin](https://github.com/kirklin) 的 [Skyward](https://github.com/kirklin/skyward) 移植适配。

Skyward 不是一个自制的 TWRP 内核，而是一个用 Rust 重写的独立 recovery：自己画界面、自己做输入与解密，
设备信息、密钥服务等全部从手机已安装系统的分区里读取，而不是硬编码在配置里。

> 本仓库只包含**源码与构建脚本**，不包含任何原厂分区镜像，刷入第三方rec前请做好备份

---

## 设备

| 项目 | 值 |
| --- | --- |
| 机型 | iQOO Neo8（V2301A） |
| 代号 | PD2301 |
| 平台 | SM8475，第一代骁龙 8+（Snapdragon 8+ Gen 1） |
| 屏幕 | 1260 × 2800，density 560 |
| 系统 | OriginOS 6 / Android 16 |
| 固件 | 16.3.17.0.W10 |
| 安全补丁 | 2026-08-01 |
| 分区 | A/B + 动态分区 |

## 功能状态

| 功能 | 状态 | 备注 |
| --- | --- | --- |
| 显示/触摸/亮度 | ✅ | --- |
| data 解密（FBE） | ✅ | 输锁屏密码解密，依赖 KeyMint + Gatekeeper |
| 安装 ZIP / 镜像 | ✅ | 含原厂完整包、boot/recovery 等分区镜像 |
| 备份 / 恢复 | ✅ | 支持 EFS 分区与 metadata |
| WLAN | ✅ | QCA6490，扫描 / 连接 / DHCP / 联网探测 |
| OTG U 盘 | ✅ | 自动挂载到 `/mnt/usb`（exfat / vfat / ntfs / ext4） |
| 震动反馈 | ✅ | 走 vivo 私有驱动的 sysfs 接口 |
| ADB / MTP | ✅ | 解密后 MTP 可访问内部存储 |
| fastbootd | ✅ | 电脑侧需能识别 `VID_18D1&PID_4EE0` 的 WinUSB 驱动 |
| 截图 | ✅ | 音量+电源，解密后写入 `DCIM/Screenshots` |

## 本移植相对上游的修改

1. **账号表补全**（`skyward-sys/src/ueventd.rs`）
   vivo 的 `passwd/group` 里只有自家 `oem_29xx` 账号，标准 AOSP 账号全靠 bionic 内置表。
   上游表缺 `inet`(3003) 等，导致 `ssgtzd` 的 socket 无法创建、整条加密服务链中断。
   已补全为 bionic `android_ids` 的完整集合。

2. **恢复环境缺失库**（`skyward-pack` 的 `--libraries`）
   - `android.hardware.security.{keymint,secureclock,sharedsecret}-V1-ndk_platform.so`
     正常系统由 VNDK APEX（`/apex/com.android.vndk.v32`）提供，recovery 不挂载 APEX；
     ramdisk 里原有的同名条目是指向 `/vendor/lib64` 的断链。
   - `libcurl.so`：vivo 把它放在 system 分区，而 `ssgtzd` 是 vendor 程序。
   同时修正了 `add_libraries` 不能替换符号链接条目的问题。

3. **WLAN vendor 服务声明**（`[wlan] services`）
   vivo 的 VINTF 只把 `ISupplicantVendor` 声明为 HIDL，但 QTI 的 wpa_supplicant 启动时按
   **AIDL** 注册；servicemanager 不认就会让它立刻退出（255）。现在随 `ISupplicant` 一并告知。

4. **OTG 主机模式**（`[usb] host_mode`）
   vivo 的 `vusb` 驱动默认只做设备端，不写 `host_mode=enabled` 就永远枚举不出 U 盘。

5. **震动**（`[haptics] timed`）
   这机器没有 force-feedback 输入设备，震动由 vivo 私有驱动控制；
   新增 sysfs 后端，写 `/sys/class/qcom-haptics/{duration,activate}`。

6. **作者信息**（`[device] authors`）
   显示在「设备信息 → Recovery 版本」下方。

## 目录结构

```
.
├── build.sh                 # 完整构建脚本
├── devices/pd2301.toml      # PD2301 设备配置
├── libs/                    # 恢复环境缺少的 4 个共享库
├── upstream/                # Skyward 源码（Rust workspace）
└── LICENSE                  # GPL-3.0
```

## 构建

依赖：

- Rust（版本由 `upstream/rust-toolchain.toml` 指定，当前 1.100.0）与 `aarch64-linux-android` target
- [`cargo-ndk`](https://github.com/bbqsrc/cargo-ndk)
- Android NDK（本项目使用 r29）

```bash
export ANDROID_NDK_HOME=/path/to/android-ndk-r29
cargo install cargo-ndk --locked
./build.sh
```

产物：`out/skyward-pd2301.img`（填满 recovery 分区，带 AVB footer）。

> 构建需要原厂 `recovery.img` 作为底包，按官方全量包自行提取后放到 `stock/recovery.img`。
> 该文件被 `.gitignore` 忽略，仓库不提供。

## 刷入

```bash
fastboot getvar current-slot        # 确认槽位，通常是 a
fastboot flash recovery_a out/skyward-pd2301.img
fastboot reboot recovery
```

要求：Bootloader 已解锁。首次进入后按提示输入锁屏密码解密 data。

### 关于 fastbootd

Skyward 的 fastbootd 使用 AOSP 标准 `VID_18D1 & PID_4EE0`，接口类为
`Class_FF & SubClass_42 & Prot_03`。Windows 自带的 `winusb.inf` 只匹配 `Prot_01`（adb），
所以**电脑上会显示为未知设备、`fastboot devices` 看不到**。
装一个支持该接口的驱动即可（Google USB Driver、OPPO/vivo 售后驱动、Zadig 绑定 WinUSB 均可）。

## 已知限制

- 解密需要用户手动输入锁屏密码，不支持默认密码。
- 端口同一时间只能是「设备」或「主机」：开 OTG 读 U 盘时 adb 会断开，拔掉后恢复。
- 未测试加密状态下的侧载 OTA。

## 开发人员名单

- 致谢：原作者[Kirk Lin](https://github.com/kirklin) —— Skyward 本体
- 适配工作：**酷安 @TNTHe、@Moecho**

免费资源，禁止倒卖。如果你是买来的，那么就是被骗了。

## 许可证

[GPL-3.0-or-later](LICENSE)。

本仓库是 Skyward 的派生作品，依 GPLv3 继续以同一许可证分发；
分发二进制时请同时提供对应源码。
