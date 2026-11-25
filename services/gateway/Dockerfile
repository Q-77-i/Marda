# P5-J2 网关镜像：Maven 构建 + JRE 21 运行。
# 同 business：target/ 由 .dockerignore 排除；构建上下文 = services/gateway。
FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn -B -q -DskipTests dependency:go-offline
COPY src ./src
RUN mvn -B -q -DskipTests package

FROM eclipse-temurin:21-jre-alpine
WORKDIR /app
COPY --from=build /app/target/*.jar app.jar
EXPOSE 8080
ENV TZ=Asia/Shanghai
ENV JAVA_TOOL_OPTIONS="-XX:MaxRAMPercentage=75"
HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=30s \
    CMD wget -qO- http://127.0.0.1:8080/actuator/health | grep -q UP || exit 1
ENTRYPOINT ["java", "-jar", "app.jar"]
