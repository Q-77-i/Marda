package com.marda.business.notification.web;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;

public record ReminderTestRequest(
        @NotBlank @Pattern(regexp = "in_app") String channel) {
}
