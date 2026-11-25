package com.marda.business.notification.web;

import com.marda.business.notification.application.ReminderService;
import jakarta.validation.Valid;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/** 测试提醒：MVP 只写 in_app pending 行；投递由 J5 的 worker 接手。 */
@RestController
@RequestMapping("/api/v1/learning")
public class ReminderController {

    private final ReminderService reminders;

    public ReminderController(ReminderService reminders) {
        this.reminders = reminders;
    }

    @PostMapping("/reminders/test")
    public ResponseEntity<ReminderTestResponse> test(
            @RequestHeader("X-User-Id") String userId,
            @Valid @RequestBody ReminderTestRequest request) {
        return ResponseEntity.status(HttpStatus.ACCEPTED)
                .body(ReminderTestResponse.from(reminders.testReminder(userId)));
    }
}
