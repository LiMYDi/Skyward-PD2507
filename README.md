# Skyward Recovery for iQOO Z10 Turbo+（PD2507 / V2507A）

vivo iQOO Z10 Turbo+ 的第三方 Recovery —— 基于 [kirklin/skyward](https://github.com/kirklin/skyward)，
经由 [Qingizi7/Skyward-PD2301](https://github.com/Qingizi7/Skyward-PD2301)（酷安 @TNTHe 的 iQOO Neo8 适配）移植而来。

> ⚠️ **本仓库产出的镜像还没有在任何真机上启动过。** 结构校验通过，但没有跑起来过，
> 刷入前请先备份原厂 `vendor_boot`，详见下文「风险」。

---

## 这台机器和 Neo8 的根本区别

PD2301（iQOO Neo8）有独立的 `recovery` 分区，刷 `recovery_<slot>` 即可。
**PD2507 没有 recovery 分区**——recovery 是 `vendor_boot` 里的一个 ramdisk 片段：

| 项目 | PD2301（Neo8） | PD2507（Z10 Turbo+） |
| --- | --- | --- |
| 平台 | SM8475，骁龙 8+ Gen 1 | **mt6991，天玑 9400+（联发科）** |
| recovery 位置 | `recovery_a` / `recovery_b` 分区 | **`vendor_boot` 内的 RECOVERY 片段（58 MB，type=2）** |
| 刷写目标 | `fastboot flash recovery_a` | 改写 `vendor_boot_a` |
| fstab | `fstab.qcom` | `first_stage_ramdisk/fstab.emmc`（`ro.hardware=emmc`） |
| 其他 | 无 `init_boot` | `boot` + `init_boot` + `vendor_boot`，动态分区 + A/B |

所以**本仓库不能照搬 PD2301 的刷写方法**，也**不能**把这里产出的镜像刷进 `recovery` 分区（那台机器没有）。

## 设备

| 项目 | 值 |
| --- | --- |
| 机型 | iQOO Z10 Turbo+（V2507A） |
| 代号 | PD2507 |
| 平台 | mt6991，天玑 9400+ |
| 屏幕 | 1260 × 2800，density 560（推断值，待实机校准） |
| 系统 | OriginOS 6 / Android 16 |
| 固件 | `PD2507_A_16.1.11.1.W10.V000L1` |
| 分区 | A/B + 动态分区，`ro.hardware=emmc` |

## 怎么出包

不需要本地装 NDK，也不需要下载 9 GB 全量包——CI 里全部搞定：

1. 仓库页 → **Actions** → `build-pd2507` → **Run workflow**
2. 约 25–40 分钟，产物在页面底部 **Artifacts**：`vendor_boot_pd2507`

流水线（`.github/workflows/build-pd2507.yml` + `tools/pd2507/build.py`）：

- 用 HTTP Range 从 vivo 官方全量包里**只取** `vendor_boot` 那一段（约 81 MB），并核对每个操作的 SHA-256；
- 解出 RECOVERY 片段的 LZ4-legacy ramdisk（115 MB 的 cpio）；
- 把它包成合成的 `ANDROID!` v4 boot 镜像，交给原版 `skyward-pack recovery` 做它本来做的事
  （替换 `system/bin/recovery`、塞 `skyward/device.toml`、字体、`system-init`、快捷命令链接、`prop.default` 追加 `ro.secure=0` 等）；
- 把装好的 ramdisk 塞回 vendor_boot 的 RECOVERY 片段，重排片段表偏移、重写 AVB footer。

容器层是字节级可逆的：对原厂镜像做「拆开再原样装回」往返，sha256 不变。

## 已验证 / 未完成

已验证（在构建产物上）：

- 分区大小正好 134,217,728 字节；
- platform 片段、dtb、bootconfig、cmdline 与原厂**逐字节相同**；
- `system/bin/recovery` 为 aarch64 ELF（8.8 MB），`system-init`、`device.toml`（codename=PD2507）、字体都在；
- `7za / avbctl / resetprop / unzip / zstd` 五个快捷命令指向 recovery；
- AVB footer 已按新镜像长度重写。

还没做：

- **真机启动**（一次都没试过）；
- 亮度：`skyward/src/ui.rs` 读的是高通 `msm_drm` 的节点与 DT 属性，MTK 要另写；
- 震动：原版 `qcom-haptics` 路径不适用；
- WLAN：MTK 的 connac 驱动与 supplicant 服务名要重新填；
- 触控模块名（原厂 recovery 用 ST FTS + Novatek，节点见 `init.recovery.touch.rc`）；
- 快充、电量等 `skyward-info` 里的高通路径。

## 风险

- 本机 `vbmeta` 的 `flags=0`，**验证启动是开着的**。解锁 BL 状态下 MTK bootloader 一般放行，但这不是保证。
- 改的是 `vendor_boot`，正常开机也会读它——**不是「只影响 recovery」**。
- 没有电脑就没有 fastboot 退路。刷之前务必：

```sh
dd if=/dev/block/by-name/vendor_boot_a of=/sdcard/vendor_boot_a_stock.img bs=1M
dd if=/dev/block/by-name/vendor_boot_b of=/sdcard/vendor_boot_b_stock.img bs=1M
```

## 致谢与许可

- 本体：[Kirk Lin](https://github.com/kirklin) 的 [Skyward](https://github.com/kirklin/skyward)
- vivo 适配：[酷安 @TNTHe / @Qingizi7](https://github.com/Qingizi7/Skyward-PD2301)、@Moecho
- 本仓库：PD2507 适配与 vendor_boot 打包流水线

GPL-3.0-or-later。本仓库是 Skyward 的派生作品，依 GPLv3 以同一许可证分发；分发二进制时请同时提供对应源码。

免费资源，禁止倒卖。如果你是买来的，那么就是被骗了。
