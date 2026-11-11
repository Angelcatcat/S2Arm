// SPDX-License-Identifier: Apache-2.0
// Copyright 2026 S2Arm contributors
namespace S2Arm.Launcher.Protocol;

/// <summary>
/// 「生成手机资源包」的输出目录（装机页 / 出包完成页都能选）：记进私有目录 phone-pack-folder.txt，下次默认还是它。
/// 空 = 不指定，作业按缺省出到 &lt;私有目录&gt;/phone_pack/&lt;游戏号&gt;/资源包（pack/jobs/phonepack.py 的 location 缺省口径）。
/// </summary>
public static class PackOutputFolder
{
    public static string PreferenceFile(string privateRoot) => Path.Combine(privateRoot, "phone-pack-folder.txt");

    /// <summary>读回上次选的目录；文件不在 / 读不了 / 文件夹已被删 → ""（按缺省走）。</summary>
    public static string Load(string privateRoot)
    {
        try
        {
            var folder = File.ReadAllText(PreferenceFile(privateRoot)).Trim();
            return folder.Length > 0 && Directory.Exists(folder) ? folder : "";
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { return ""; }
    }

    /// <summary>记下（null / 空 = 恢复缺省，删掉记录）。</summary>
    public static void Save(string privateRoot, string? folder)
    {
        var file = PreferenceFile(privateRoot);
        try
        {
            if (string.IsNullOrWhiteSpace(folder))
            {
                if (File.Exists(file)) File.Delete(file);
                return;
            }
            Directory.CreateDirectory(privateRoot);
            File.WriteAllText(file, Path.GetFullPath(folder));
        }
        catch (Exception error) when (error is IOException or UnauthorizedAccessException) { }
    }
}
