# Maven 推送工具

这是一个用于“从本地 Maven 仓库批量补推构件到远程仓库”的命令行工具。

工具职责如下：

- 扫描本地 Maven 仓库中的版本目录
- 按 `gav`、`group-prefix`、`input-file`、`scan-subpath` 等规则筛选构件
- 从 POM 解析坐标并做一致性校验
- 自动判断推送到 `release` 还是 `snapshot`
- 对 `release` 做远程存在性预检
- 最终通过 Maven 官方的 `deploy:deploy-file` 执行上传

## 运行前提

无论是直接运行 Python 脚本，还是运行打包后的可执行文件，目标机器都需要：

- 可用的 `mvn` 命令，或者通过 `--mvn-bin` 显式指定 Maven 可执行路径
- 可访问目标私服
- 可用的 Maven `settings.xml`

`settings.xml` 的读取规则如下：

- 如果传入 `--settings-file`，优先使用指定文件
- 如果未传入，自动尝试默认用户目录下的 `~/.m2/settings.xml`
- 如果命令行传了 `--username` 和 `--password`，工具会在运行时生成临时 `settings.xml` 供 Maven 使用

## 直接运行

```bash
python push_maven_local.py \
  --gav com.example:demo:1.0.0 \
  --release-repo-id releases \
  --release-repo-url http://nexus/repository/maven-releases/ \
  --snapshot-repo-id snapshots \
  --snapshot-repo-url http://nexus/repository/maven-snapshots/ \
  --dry-run
```

Windows 示例：

```bat
python push_maven_local.py ^
  --gav com.example:demo:1.0.0 ^
  --release-repo-id releases ^
  --release-repo-url http://nexus/repository/maven-releases/ ^
  --snapshot-repo-id snapshots ^
  --snapshot-repo-url http://nexus/repository/maven-snapshots/ ^
  --dry-run
```

## 打包

本项目使用 `PyInstaller` 打包为单文件可执行程序。

注意：

- Windows 可执行文件建议在 Windows 上构建
- Linux 可执行文件建议在 Linux 上构建
- `PyInstaller` 不建议拿同一份构建产物跨平台使用

### Windows 打包

在 Windows 机器上执行：

```bat
build_windows.bat
```

产物位置：

```text
dist\windows\maven-push-tool.exe
```

### Linux 打包

在 Linux 机器上执行：

```bash
chmod +x build_linux.sh
./build_linux.sh
```

产物位置：

```text
dist/linux/maven-push-tool
```

## 打包后运行

### Windows

```bat
dist\windows\maven-push-tool.exe ^
  --settings-file C:\Users\yourname\.m2\settings.xml ^
  --gav com.example:demo:1.0.0 ^
  --release-repo-id releases ^
  --release-repo-url http://nexus/repository/maven-releases/ ^
  --snapshot-repo-id snapshots ^
  --snapshot-repo-url http://nexus/repository/maven-snapshots/ ^
  --log-file logs\run.log ^
  --failed-file logs\failed.csv ^
  --report-file logs\report.json ^
  --dry-run
```

### Linux

```bash
./dist/linux/maven-push-tool \
  --settings-file /home/yourname/.m2/settings.xml \
  --gav com.example:demo:1.0.0 \
  --release-repo-id releases \
  --release-repo-url http://nexus/repository/maven-releases/ \
  --snapshot-repo-id snapshots \
  --snapshot-repo-url http://nexus/repository/maven-snapshots/ \
  --log-file logs/run.log \
  --failed-file logs/failed.csv \
  --report-file logs/report.json \
  --dry-run
```

如果不传 `--settings-file`，则会自动读取当前用户默认位置的 Maven 配置。

## 常用筛选方式

### 指定单个 GAV

```bash
--gav com.example:demo:1.0.0
```

### 指定某个 artifact 的所有版本

```bash
--gav com.example:demo
```

### 按 groupId 前缀筛选

```bash
--group-prefix com.company
```

### 从文件读取清单

```bash
--input-file artifacts.txt
```

文件内容示例：

```text
com.example:demo:1.0.0
com.example:demo-api
```

## 输出文件

运行过程中可输出以下文件：

- `--log-file`：运行日志
- `--failed-file`：失败清单 CSV
- 与 `failed.csv` 同目录生成 `failed.jsonl`
- `--report-file`：汇总报告 JSON

## 建议的首次运行方式

首次执行建议始终带上：

```bash
--dry-run
```

先确认：

- 扫描结果是否正确
- 筛选结果是否符合预期
- `release` 预检是否正常
- 目标仓库是否判定正确

确认无误后，再去掉 `--dry-run` 执行真实推送。
