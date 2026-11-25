package com.marda.business.event;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;

/** `notification.requested` payload：只带定位信息，内容在 notification 行。 */
public record NotificationRequestedPayload(
        @NotBlank String notificationId,
        @NotBlank String userId,
        @NotBlank @Pattern(regexp = "in_app") String channel,
        @NotBlank @Size(max = 64) String template,
        @Size(max = 200) String idempotencyKey) {
}
