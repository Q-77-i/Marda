"""代码工作台的算法题面目录（M15 补强）。

题库里的算法题**很多只有标题或代码答案**，没有输入/输出/示例——直接拿来当机试题
候选人无法作答（实测「最小合法子串」只有五个字，而它的答案其实是「最长有效括号」）。
这里按 question_id 提供**我们自己撰写的**题面：标题 / 描述 / 示例 / 约束。

口径（同 M15 计划）：**git 里只存题库 id 和我们的文本**，不复用题库原文；
随机题优先从本目录抽，目录外的题回退到题库原文（测试/旧库兜底）。
题库 id 是内容哈希，同一份语料重跑 ingest 应保持稳定。
"""

from __future__ import annotations

from typing import NamedTuple


class CodingProblem(NamedTuple):
    title: str
    description: str
    examples: tuple[str, ...]
    constraints: str = ""


CODING_PROBLEMS: dict[str, CodingProblem] = {
    "q_346abeed8c62": CodingProblem(
        title="反转链表",
        description=(
            "给定一个单链表的头节点 head，反转链表并返回新的头节点。"
            "请分别给出迭代与递归两种写法，并说明各自的时间/空间复杂度。"
        ),
        examples=(
            "输入：1 → 2 → 3 → 4 → 5\n输出：5 → 4 → 3 → 2 → 1",
            "输入：head = None\n输出：None",
        ),
        constraints="节点数 0–5000；节点值 -5000–5000。",
    ),
    "q_d21e5c8d9dcd": CodingProblem(
        title="岛屿最大面积",
        description=(
            "给定 m×n 的 0/1 网格，1 表示陆地、0 表示水。"
            "岛屿由上下左右相邻的陆地连接而成，返回所有岛屿中的最大面积；没有岛屿返回 0。"
        ),
        examples=(
            "输入：grid = [[0,0,1,0],[0,1,1,0],[0,0,0,1]]\n输出：3",
            "输入：grid = [[0,0,0]]\n输出：0",
        ),
        constraints="1 ≤ m,n ≤ 50；只能上下左右移动。",
    ),
    "q_68c9ca431926": CodingProblem(
        title="最长公共前缀",
        description="给定字符串数组 strs，返回所有字符串的最长公共前缀；不存在公共前缀时返回空字符串。",
        examples=(
            '输入：["flower","flow","flight"]\n输出："fl"',
            '输入：["dog","racecar","car"]\n输出：""',
        ),
        constraints="0 ≤ strs.length ≤ 200；字符串只含小写字母。",
    ),
    "q_8a6821c8e725": CodingProblem(
        title="最大子数组和",
        description="给定整数数组 nums，找到一个和最大的连续子数组（至少包含一个元素），返回该子数组的最大和。",
        examples=(
            "输入：[-2,1,-3,4,-1,2,1,-5,4]\n输出：6（子数组 [4,-1,2,1]）",
            "输入：[1]\n输出：1",
        ),
        constraints="1 ≤ nums.length ≤ 10^5；-10^4 ≤ nums[i] ≤ 10^4。",
    ),
    "q_9f1198e0a2c8": CodingProblem(
        title="斐波那契数列",
        description=(
            "求斐波那契数列第 n 项：F(0)=0，F(1)=1，F(n)=F(n-1)+F(n-2)。"
            "n 为非负整数；要求时间 O(n)、空间 O(1)。"
        ),
        examples=("输入：n = 0\n输出：0", "输入：n = 6\n输出：8"),
        constraints="0 ≤ n ≤ 90。",
    ),
    "q_be84d359f69e": CodingProblem(
        title="二分查找",
        description="给定升序整数数组 nums 和目标值 target，返回 target 在数组中的下标；不存在返回 -1。要求 O(log n)。",
        examples=(
            "输入：nums = [-1,0,3,5,9,12], target = 9\n输出：4",
            "输入：nums = [-1,0,3,5,9,12], target = 2\n输出：-1",
        ),
        constraints="1 ≤ nums.length ≤ 10^4；数组严格升序。",
    ),
    "q_1b5dedf8a2b1": CodingProblem(
        title="合并区间",
        description="给定若干区间 [start, end]，合并所有重叠或相邻的区间，返回不重叠的区间列表（按起点升序）。",
        examples=(
            "输入：[[1,3],[2,6],[8,10],[15,18]]\n输出：[[1,6],[8,10],[15,18]]",
            "输入：[[1,4],[4,5]]\n输出：[[1,5]]",
        ),
        constraints="1 ≤ intervals.length ≤ 10^4；0 ≤ start ≤ end ≤ 10^4。",
    ),
    "q_44464bcf4c4c": CodingProblem(
        title="打开转盘锁",
        description=(
            "转盘锁有四位数字，每次可以把任意一位向上/向下拨动一格（0↔9 环绕）。"
            '从 "0000" 出发，不能经过 deadends 中的状态，求到达 target 的最少拨动次数；'
            "无法到达返回 -1。"
        ),
        examples=(
            '输入：deadends = ["0201","0101","0102","1212","2002"], target = "0202"\n输出：6',
            '输入：deadends = ["8888"], target = "0009"\n输出：1',
        ),
        constraints="1 ≤ deadends.length ≤ 500；target 是四位数字字符串。",
    ),
    "q_5cc2240fd2e4": CodingProblem(
        title="三数之和",
        description="给定整数数组 nums，返回所有和为 0 且不重复的三元组 [nums[i], nums[j], nums[k]]。",
        examples=(
            "输入：[-1,0,1,2,-1,-4]\n输出：[[-1,-1,2],[-1,0,1]]",
            "输入：[0,1,1]\n输出：[]",
        ),
        constraints="0 ≤ nums.length ≤ 3000；答案中三元组不能重复。",
    ),
    "q_7353a716de4e": CodingProblem(
        title="最长无重复字符子串",
        description="给定字符串 s，返回不含重复字符的最长子串长度。",
        examples=(
            '输入："abcabcbb"\n输出：3（"abc"）',
            '输入："bbbbb"\n输出：1',
        ),
        constraints="0 ≤ s.length ≤ 5×10^4；s 由英文字母、数字、符号和空格组成。",
    ),
    "q_c1bd06294d9d": CodingProblem(
        title="数组中的 Top-K",
        description=(
            "给定整数数组 nums 和整数 k，返回数组中前 k 大的元素（顺序不限）。"
            "请说明排序、小顶堆、快速选择三种解法的复杂度与适用场景。"
        ),
        examples=(
            "输入：nums = [3,2,1,5,6,4], k = 2\n输出：[5,6]（顺序不限）",
            "输入：nums = [1], k = 1\n输出：[1]",
        ),
        constraints="1 ≤ k ≤ nums.length ≤ 10^5。",
    ),
    "q_cb316aba77d2": CodingProblem(
        title="找到所有字母异位词",
        description=(
            "给定字符串 s 和 p，返回 s 中所有 p 的字母异位词的起始下标"
            "（字母异位词由相同字母重排而成，顺序不限）。"
        ),
        examples=(
            '输入：s = "cbaebabacd", p = "abc"\n输出：[0,6]',
            '输入：s = "abab", p = "ab"\n输出：[0,1,2]',
        ),
        constraints="1 ≤ s.length, p.length ≤ 3×10^4；只含小写字母。",
    ),
    "q_b7b867da3def": CodingProblem(
        title="最长回文子串",
        description="给定字符串 s，返回 s 中最长的回文子串（回文指正着读和反着读相同）。",
        examples=(
            '输入："babad"\n输出："bab"（"aba" 也可）',
            '输入："cbbd"\n输出："bb"',
        ),
        constraints="1 ≤ s.length ≤ 1000；只含字母和数字。",
    ),
    # 题库标题「最小合法子串」与答案（longestValidParentheses）不符；这里按答案纠正标题。
    "q_ad5d3da72f4f": CodingProblem(
        title="最长有效括号",
        description="给定一个只包含 '(' 和 ')' 的字符串 s，返回最长有效（格式正确且连续）括号子串的长度。",
        examples=(
            '输入："(()"\n输出：2（"()"）',
            '输入：")()())"\n输出：4（"()()"）',
        ),
        constraints="0 ≤ s.length ≤ 3×10^4。",
    ),
}


def format_problem(problem: CodingProblem) -> str:
    """题面 → 点评 prompt 用的纯文本（标题 / 描述 / 示例 / 约束）。"""
    parts = [f"【{problem.title}】", problem.description]
    if problem.examples:
        parts.append("示例：\n\n" + "\n\n".join(problem.examples))
    if problem.constraints:
        parts.append(f"约束：{problem.constraints}")
    return "\n\n".join(parts)
