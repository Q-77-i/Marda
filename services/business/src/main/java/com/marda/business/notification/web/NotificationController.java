package com.marda.business.notification.web;

import com.marda.business.common.BadRequestException;
import com.marda.business.notification.domain.Notification;
import com.marda.business.notification.domain.NotificationStatus;
import com.marda.business.notification.persistence.NotificationRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

/**
 * 通知读接口（J3 明确留到 J5）：默认只返回 {@code sent}（对用户可见的送达）。
 *
 * <p>用户隔离由网关注入的 {@code X-User-Id} 保证；跨用户永远查不到。
 */
@RestController
@RequestMapping("/api/v1/learning")
public class NotificationController {

    private final NotificationRepository notifications;
    private final ObjectMapper objectMapper;

    public NotificationController(
            NotificationRepository notifications, ObjectMapper objectMapper) {
        this.notifications = notifications;
        this.objectMapper = objectMapper;
    }

    @GetMapping("/notifications")
    public NotificationListResponse list(
            @RequestHeader("X-User-Id") String userId,
            @RequestParam(defaultValue = "sent") String status,
            @RequestParam(defaultValue = "20") @Min(1) @Max(50) int limit) {
        NotificationStatus parsed;
        try {
            parsed = NotificationStatus.fromApi(status);
        } catch (IllegalArgumentException exception) {
            throw new BadRequestException(exception.getMessage());
        }
        List<NotificationView> items = notifications.listByUserId(userId, parsed, limit)
                .stream()
                .map(this::view)
                .toList();
        return new NotificationListResponse(items);
    }

    private NotificationView view(Notification notification) {
        JsonNode payload;
        try {
            payload = objectMapper.readTree(notification.payloadJson());
        } catch (Exception exception) {
            payload = objectMapper.createObjectNode();
        }
        return new NotificationView(
                notification.id(),
                notification.template(),
                payload,
                notification.status().apiValue(),
                notification.createdAt(),
                notification.sentAt());
    }

    public record NotificationListResponse(List<NotificationView> items) {
    }

    public record NotificationView(
            UUID id,
            String template,
            JsonNode payload,
            String status,
            Instant createdAt,
            Instant sentAt) {
    }
}
