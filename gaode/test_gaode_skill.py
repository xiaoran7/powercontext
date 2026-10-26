from pathlib import Path


def test_gaode_skill_structure_and_rules():
    skill_path = Path(__file__).parent / "SKILL.md"
    assert skill_path.exists(), "gaode/SKILL.md must exist"
    content = skill_path.read_text(encoding="utf-8")

    # 1. YAML Frontmatter 校验
    assert content.startswith("---\n")
    assert "name: gaode-nearby-explorer" in content
    assert "description:" in content

    # 2. 必备高德 MCP 工具与四步工作流校验
    required_tools = [
        "maps_around_search",
        "maps_text_search",
        "maps_direction_walking",
    ]
    for tool in required_tools:
        assert tool in content, f"Missing required AMap MCP tool: {tool}"

    # 3. 核心过滤、排序、输出结构与异常兜底校验
    required_rules = [
        "<= 15 分钟",
        "未核实",
        "推荐理由",
        "宁缺毋滥",
        "OB-P:xI1C-CXPJXT4ztVpZDY6_A",
    ]
    for rule in required_rules:
        assert rule in content, f"Missing required rule or verification line: {rule}"
