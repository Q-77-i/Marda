package com.marda.business.plan.domain;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * 幂等请求哈希：同一份建计划输入必须得到同一个值，空白文本归一为空串。
 *
 * <p>只用于区分「同一 Idempotency-Key 下的不同请求体」；不包含 userId（请求体里没有），
 * 也不包含任何密钥。哈希本身不是安全边界，只是冲突判据。
 */
public final class IdempotencyHasher {

    private final ObjectMapper mapper;

    public IdempotencyHasher(ObjectMapper mapper) {
        this.mapper = mapper;
    }

    public String hash(PlanInput input) {
        Map<String, Object> canonical = new TreeMap<>();
        canonical.put("source_interview_id", normalize(input.sourceInterviewId()));
        canonical.put("goal", normalize(input.goal()));
        canonical.put("position", normalize(input.position()));
        canonical.put("interview_type", input.interviewType() == null ? "tech" : input.interviewType());
        canonical.put("report_summary", reportSummary(input.reportSummary()));
        try {
            byte[] bytes = mapper.writeValueAsString(canonical).getBytes(StandardCharsets.UTF_8);
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            return HexFormat.of().formatHex(digest.digest(bytes));
        } catch (NoSuchAlgorithmException | com.fasterxml.jackson.core.JsonProcessingException exc) {
            throw new IllegalStateException("无法计算幂等哈希", exc);
        }
    }

    private static Map<String, Object> reportSummary(ReportSummary summary) {
        Map<String, Object> canonical = new TreeMap<>();
        canonical.put("overall", summary == null ? 0.0 : summary.overall());
        List<Map<String, Object>> weakDomains = new ArrayList<>();
        if (summary != null && summary.weakDomains() != null) {
            for (WeakDomain weakDomain : summary.weakDomains()) {
                if (weakDomain == null) {
                    continue;
                }
                Map<String, Object> item = new TreeMap<>();
                item.put("domain", normalize(weakDomain.domain()));
                item.put("score", weakDomain.score());
                item.put("advice", normalize(weakDomain.advice()));
                weakDomains.add(item);
            }
        }
        canonical.put("weak_domains", weakDomains);
        return canonical;
    }

    private static String normalize(String value) {
        return value == null ? "" : value.trim();
    }
}
